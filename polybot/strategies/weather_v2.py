"""
Enhanced Weather Arbitrage Strategy v2

Uses Gaussian probability distributions for temperature predictions
and proper statistical models for precipitation forecasting.
Designed for latency-tolerant execution (Mac Mini friendly).

Key improvements over v1:
1. Gaussian CDF for temperature probability calculation
2. Historical calibration of NOAA forecast accuracy
3. Confidence-weighted position sizing
4. Better edge detection with statistical significance
"""

import asyncio
import math
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from loguru import logger

from polybot.connectors.noaa import NOAAConnector, DailyForecast
from polybot.connectors.polymarket import PolymarketConnector
from polybot.core.simulation import SimulationEngine
from polybot.models import Market, Order, OrderSide, OutcomeType


@dataclass
class ForecastConfidence:
    """NOAA forecast accuracy calibration data."""
    
    # Historical RMSE for temperature forecasts by lead time (hours)
    # Source: NOAA forecast verification studies
    TEMP_RMSE_BY_LEAD = {
        24: 2.5,   # 1 day out: ±2.5°F typical error
        48: 3.5,   # 2 days out
        72: 4.5,   # 3 days out
        120: 6.0,  # 5 days out
        168: 7.5,  # 7 days out
    }
    
    # Precipitation forecast reliability (Brier skill score derived)
    PRECIP_CALIBRATION = {
        "0-10": 0.95,    # When NOAA says <10%, it happens ~5%
        "10-30": 0.85,   # Slight overforecast tendency
        "30-50": 0.90,   # Well calibrated
        "50-70": 0.90,   # Well calibrated
        "70-90": 0.85,   # Slight overforecast
        "90-100": 0.92,  # High confidence events reliable
    }


def gaussian_cdf(x: float, mean: float, std: float) -> float:
    """
    Calculate cumulative distribution function for Gaussian.
    
    P(X <= x) where X ~ N(mean, std^2)
    """
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))


def get_lead_time_hours(target_date: str) -> int:
    """Calculate hours between now and target date."""
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d")
        now = datetime.utcnow()
        delta = target - now
        return max(24, int(delta.total_seconds() / 3600))
    except:
        return 24


@dataclass
class WeatherSignal:
    """A statistically-grounded weather trading signal."""
    
    market: Market
    market_prob: Decimal
    forecast_prob: Decimal
    forecast_std: Decimal  # Standard deviation of our probability estimate
    recommended_side: OutcomeType
    edge: Decimal
    edge_zscore: float  # Statistical significance
    confidence: str  # HIGH, MEDIUM, LOW
    kelly_fraction: float  # Optimal bet sizing (Kelly criterion)
    reasoning: str
    
    @property
    def expected_value(self) -> Decimal:
        """Expected value per dollar risked."""
        if self.recommended_side == OutcomeType.YES:
            win_prob = self.forecast_prob
            cost = self.market_prob
        else:
            win_prob = Decimal("1") - self.forecast_prob
            cost = Decimal("1") - self.market_prob
        
        # EV = P(win) * $1 - cost
        return win_prob * Decimal("1") - cost
    
    @property
    def is_statistically_significant(self) -> bool:
        """Check if edge is statistically significant (z > 2)."""
        return self.edge_zscore >= 2.0
    
    def __repr__(self) -> str:
        return (
            f"WeatherSignal({self.market.question[:40]}... | "
            f"Market: {self.market_prob:.0%} vs Forecast: {self.forecast_prob:.0%}±{self.forecast_std:.0%} | "
            f"Buy {self.recommended_side.value} | Edge: {self.edge:.1%} (z={self.edge_zscore:.1f}) | "
            f"Kelly: {self.kelly_fraction:.1%})"
        )


class WeatherStrategyV2:
    """
    Enhanced weather arbitrage strategy with statistical modeling.
    
    Uses Gaussian distributions for temperature and calibrated
    probabilities for precipitation to find statistically
    significant edges.
    """

    WEATHER_KEYWORDS = [
        "rain", "snow", "temperature", "degrees", "°f", "°c",
        "weather", "storm", "hurricane", "tornado", "heat",
        "cold", "freeze", "frost", "precipitation", "sunny",
        "cloudy", "wind", "mph", "inches", "high", "low",
    ]

    CITY_PATTERNS = [
        (r"new york|nyc|manhattan", "New York"),
        (r"los angeles|la\b|hollywood", "Los Angeles"),
        (r"chicago", "Chicago"),
        (r"houston", "Houston"),
        (r"phoenix", "Phoenix"),
        (r"philadelphia|philly", "Philadelphia"),
        (r"san antonio", "San Antonio"),
        (r"san diego", "San Diego"),
        (r"dallas", "Dallas"),
        (r"miami", "Miami"),
        (r"denver", "Denver"),
        (r"seattle", "Seattle"),
        (r"boston", "Boston"),
        (r"atlanta", "Atlanta"),
        (r"las vegas|vegas", "Las Vegas"),
        (r"washington|dc|d\.c\.", "Washington DC"),
        (r"detroit", "Detroit"),
        (r"minneapolis", "Minneapolis"),
        (r"san francisco|sf\b", "San Francisco"),
        (r"tampa", "Tampa"),
    ]

    def __init__(
        self,
        polymarket: PolymarketConnector,
        noaa: NOAAConnector,
        simulation: Optional[SimulationEngine] = None,
        min_edge_percent: Decimal = Decimal("10"),  # 10% edge minimum
        min_zscore: float = 1.5,  # Minimum statistical significance
        max_kelly_fraction: float = 0.25,  # Cap Kelly at 25% of suggested
    ):
        self.polymarket = polymarket
        self.noaa = noaa
        self.simulation = simulation
        self.min_edge = min_edge_percent / 100
        self.min_zscore = min_zscore
        self.max_kelly_fraction = max_kelly_fraction
        
        self._running = False
        self._signals: list[WeatherSignal] = []

    def _is_weather_market(self, market: Market) -> bool:
        """Check if market is weather-related."""
        question_lower = market.question.lower()
        return any(kw in question_lower for kw in self.WEATHER_KEYWORDS)

    def _extract_city(self, question: str) -> Optional[str]:
        """Extract city from market question."""
        question_lower = question.lower()
        for pattern, city in self.CITY_PATTERNS:
            if re.search(pattern, question_lower):
                return city
        return None

    def _extract_date(self, question: str) -> Optional[str]:
        """Extract target date from market question."""
        question_lower = question.lower()
        now = datetime.utcnow()
        
        # Check for relative dates
        if "tomorrow" in question_lower:
            return (now + timedelta(days=1)).strftime("%Y-%m-%d")
        if "today" in question_lower:
            return now.strftime("%Y-%m-%d")
        
        # Check for "this weekend", "next week", etc.
        if "this weekend" in question_lower:
            days_until_saturday = (5 - now.weekday()) % 7
            return (now + timedelta(days=days_until_saturday)).strftime("%Y-%m-%d")
        
        # Check for specific date patterns
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        
        for month_name, month_num in month_names.items():
            pattern = rf"{month_name}\s+(\d{{1,2}})"
            match = re.search(pattern, question_lower)
            if match:
                day = int(match.group(1))
                year = now.year if month_num >= now.month else now.year + 1
                try:
                    return datetime(year, month_num, day).strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        # Default to tomorrow
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    def _calculate_temperature_probability(
        self,
        forecast_temp: float,
        threshold: float,
        lead_time_hours: int,
        is_above: bool = True,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate probability that actual temp will be above/below threshold
        using Gaussian distribution based on forecast uncertainty.
        
        Returns: (probability, standard_deviation)
        """
        # Get appropriate RMSE based on lead time
        rmse_map = ForecastConfidence.TEMP_RMSE_BY_LEAD
        
        if lead_time_hours <= 24:
            std = rmse_map[24]
        elif lead_time_hours <= 48:
            std = rmse_map[48]
        elif lead_time_hours <= 72:
            std = rmse_map[72]
        elif lead_time_hours <= 120:
            std = rmse_map[120]
        else:
            std = rmse_map[168]
        
        # P(actual_temp > threshold) = 1 - CDF(threshold)
        # P(actual_temp < threshold) = CDF(threshold)
        
        if is_above:
            prob = 1 - gaussian_cdf(threshold, forecast_temp, std)
        else:
            prob = gaussian_cdf(threshold, forecast_temp, std)
        
        # Clamp to reasonable bounds
        prob = max(0.01, min(0.99, prob))
        
        # Calculate uncertainty in the probability estimate
        # This comes from uncertainty in the forecast itself
        prob_std = std / (forecast_temp - threshold + 0.01) * 0.1  # Rough approximation
        prob_std = max(0.05, min(0.20, abs(prob_std)))
        
        return Decimal(str(round(prob, 4))), Decimal(str(round(prob_std, 4)))

    def _calculate_kelly(
        self,
        win_prob: Decimal,
        odds: Decimal,
    ) -> float:
        """
        Calculate Kelly criterion for optimal bet sizing.
        
        f* = (p * b - q) / b
        where:
        - p = probability of winning
        - q = probability of losing (1-p)
        - b = odds received (payout / stake - 1)
        
        For prediction markets:
        - We pay 'cost' for a share
        - We receive $1 if we win
        - So b = (1/cost) - 1
        """
        p = float(win_prob)
        q = 1 - p
        
        if odds <= 0 or odds >= 1:
            return 0.0
        
        # Odds: if we pay 0.40, we get 2.5x on win (b = 1.5)
        b = (1.0 / float(odds)) - 1
        
        if b <= 0:
            return 0.0
        
        kelly = (p * b - q) / b
        
        # Return fractional Kelly for safety
        return max(0, kelly * 0.25)  # Quarter Kelly

    async def analyze_temperature_market(
        self,
        market: Market,
        city: str,
        date: str,
    ) -> Optional[WeatherSignal]:
        """
        Analyze temperature prediction market using Gaussian model.
        """
        try:
            high, low = await self.noaa.get_temperature_forecast(city, date)
            if high is None:
                return None
            
            question_lower = market.question.lower()
            
            # Extract temperature threshold
            temp_match = re.search(r"(\d+)\s*(?:degrees|°|f)", question_lower)
            if not temp_match:
                return None
            
            threshold = int(temp_match.group(1))
            lead_hours = get_lead_time_hours(date)
            
            # Determine market type
            is_high_temp = "high" in question_lower or "reach" in question_lower
            is_above = "above" in question_lower or "over" in question_lower or "exceed" in question_lower or "at least" in question_lower
            is_below = "below" in question_lower or "under" in question_lower or "less than" in question_lower
            
            # Choose forecast value
            forecast_temp = high if is_high_temp or not is_below else low
            
            # Calculate probability using Gaussian model
            forecast_prob, prob_std = self._calculate_temperature_probability(
                forecast_temp=forecast_temp,
                threshold=threshold,
                lead_time_hours=lead_hours,
                is_above=is_above or not is_below,
            )
            
            market_prob = market.yes_price
            edge = abs(forecast_prob - market_prob)
            
            # Calculate z-score for statistical significance
            combined_std = float(prob_std) + 0.05  # Add market uncertainty
            zscore = float(edge) / combined_std if combined_std > 0 else 0
            
            if edge < self.min_edge or zscore < self.min_zscore:
                return None
            
            # Determine trade direction
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                cost = market_prob
                reasoning = (
                    f"Gaussian model: {forecast_temp}°F forecast → "
                    f"P(>{threshold}°F)={forecast_prob:.0%}±{prob_std:.0%}, "
                    f"market pricing {market_prob:.0%}"
                )
            else:
                recommended = OutcomeType.NO
                cost = Decimal("1") - market_prob
                reasoning = (
                    f"Gaussian model: {forecast_temp}°F forecast → "
                    f"P(>{threshold}°F)={forecast_prob:.0%}±{prob_std:.0%}, "
                    f"market overpricing at {market_prob:.0%}"
                )
            
            # Calculate Kelly fraction
            win_prob = forecast_prob if recommended == OutcomeType.YES else (Decimal("1") - forecast_prob)
            kelly = self._calculate_kelly(win_prob, cost)
            kelly = min(kelly, self.max_kelly_fraction)
            
            # Assign confidence
            if zscore >= 3.0 and edge >= Decimal("0.20"):
                confidence = "HIGH"
            elif zscore >= 2.0 and edge >= Decimal("0.12"):
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            return WeatherSignal(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                forecast_std=prob_std,
                recommended_side=recommended,
                edge=edge,
                edge_zscore=zscore,
                confidence=confidence,
                kelly_fraction=kelly,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze temperature market: {e}")
            return None

    async def analyze_rain_market(
        self,
        market: Market,
        city: str,
        date: str,
    ) -> Optional[WeatherSignal]:
        """Analyze rain prediction market with calibrated probabilities."""
        try:
            will_rain, raw_prob = await self.noaa.will_it_rain(city, date)
            
            # Calibrate NOAA probability based on historical accuracy
            if raw_prob < 10:
                calibration = 0.95
            elif raw_prob < 30:
                calibration = 0.85
            elif raw_prob < 50:
                calibration = 0.90
            elif raw_prob < 70:
                calibration = 0.90
            elif raw_prob < 90:
                calibration = 0.85
            else:
                calibration = 0.92
            
            # Apply calibration (slight regression to mean)
            calibrated_prob = raw_prob * calibration + 50 * (1 - calibration)
            forecast_prob = Decimal(str(calibrated_prob / 100))
            
            # Uncertainty in precipitation forecasts is higher
            prob_std = Decimal("0.12")
            
            market_prob = market.yes_price
            edge = abs(forecast_prob - market_prob)
            
            combined_std = float(prob_std) + 0.05
            zscore = float(edge) / combined_std if combined_std > 0 else 0
            
            if edge < self.min_edge or zscore < self.min_zscore:
                return None
            
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                cost = market_prob
                reasoning = f"NOAA says {raw_prob}% (calibrated: {calibrated_prob:.0f}%), market pricing {market_prob:.0%}"
            else:
                recommended = OutcomeType.NO
                cost = Decimal("1") - market_prob
                reasoning = f"NOAA says only {raw_prob}% (calibrated: {calibrated_prob:.0f}%), market pricing {market_prob:.0%}"
            
            win_prob = forecast_prob if recommended == OutcomeType.YES else (Decimal("1") - forecast_prob)
            kelly = self._calculate_kelly(win_prob, cost)
            kelly = min(kelly, self.max_kelly_fraction)
            
            if zscore >= 2.5 and edge >= Decimal("0.18"):
                confidence = "HIGH"
            elif zscore >= 1.8 and edge >= Decimal("0.10"):
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            return WeatherSignal(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                forecast_std=prob_std,
                recommended_side=recommended,
                edge=edge,
                edge_zscore=zscore,
                confidence=confidence,
                kelly_fraction=kelly,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze rain market: {e}")
            return None

    async def analyze_snow_market(
        self,
        market: Market,
        city: str,
        date: str,
    ) -> Optional[WeatherSignal]:
        """Analyze snow prediction market."""
        try:
            will_snow, prob = await self.noaa.will_it_snow(city, date)
            
            # Snow is binary-ish: either conditions support it or not
            if will_snow:
                forecast_prob = Decimal(str(min(0.90, prob / 100 + 0.20)))
            else:
                forecast_prob = Decimal(str(max(0.05, prob / 100)))
            
            prob_std = Decimal("0.15")  # Snow forecasts have higher uncertainty
            
            market_prob = market.yes_price
            edge = abs(forecast_prob - market_prob)
            
            combined_std = float(prob_std) + 0.05
            zscore = float(edge) / combined_std if combined_std > 0 else 0
            
            if edge < self.min_edge or zscore < self.min_zscore:
                return None
            
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                cost = market_prob
                reasoning = f"NOAA forecasts snow conditions ({prob}%), market only pricing {market_prob:.0%}"
            else:
                recommended = OutcomeType.NO
                cost = Decimal("1") - market_prob
                reasoning = f"NOAA shows no snow expected, market overpricing at {market_prob:.0%}"
            
            win_prob = forecast_prob if recommended == OutcomeType.YES else (Decimal("1") - forecast_prob)
            kelly = self._calculate_kelly(win_prob, cost)
            kelly = min(kelly, self.max_kelly_fraction)
            
            if zscore >= 2.5 and edge >= Decimal("0.25"):
                confidence = "HIGH"
            elif zscore >= 1.8 and edge >= Decimal("0.15"):
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            return WeatherSignal(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                forecast_std=prob_std,
                recommended_side=recommended,
                edge=edge,
                edge_zscore=zscore,
                confidence=confidence,
                kelly_fraction=kelly,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze snow market: {e}")
            return None

    async def scan_markets(self) -> list[WeatherSignal]:
        """Scan all markets for weather opportunities with statistical significance."""
        logger.info("🌦️ Scanning for weather arbitrage (v2 Gaussian model)...")
        
        signals = []
        markets = await self.polymarket.fetch_markets()
        
        weather_markets = [m for m in markets if self._is_weather_market(m)]
        logger.info(f"Found {len(weather_markets)} weather-related markets")
        
        for market in weather_markets:
            city = self._extract_city(market.question)
            if not city:
                continue
            
            date = self._extract_date(market.question)
            question_lower = market.question.lower()
            
            signal = None
            
            if any(kw in question_lower for kw in ["temperature", "degrees", "°", "high", "low", "reach"]):
                signal = await self.analyze_temperature_market(market, city, date)
            elif "rain" in question_lower or "precipitation" in question_lower:
                signal = await self.analyze_rain_market(market, city, date)
            elif "snow" in question_lower:
                signal = await self.analyze_snow_market(market, city, date)
            
            if signal and signal.is_statistically_significant:
                signals.append(signal)
                logger.info(f"🌦️ SIGNAL: {signal}")
            elif signal:
                logger.debug(f"Weak signal (z={signal.edge_zscore:.1f}): {market.question[:50]}")
        
        self._signals = sorted(signals, key=lambda x: (x.confidence == "HIGH", x.edge_zscore), reverse=True)
        logger.info(f"Found {len(self._signals)} statistically significant weather signals")
        return self._signals

    async def execute_signal(
        self,
        signal: WeatherSignal,
        capital: Decimal,
    ) -> Optional[Order]:
        """
        Execute a weather signal with Kelly-optimal sizing.
        
        Args:
            signal: The weather signal to execute
            capital: Available capital for this trade
        """
        if signal.confidence == "LOW":
            logger.warning("Skipping LOW confidence signal")
            return None
        
        # Calculate position size using Kelly fraction
        size = capital * Decimal(str(signal.kelly_fraction))
        size = max(Decimal("1"), min(size, Decimal("100")))  # $1-$100 range
        
        price = (
            signal.market.yes_price
            if signal.recommended_side == OutcomeType.YES
            else signal.market.no_price
        )
        
        if self.simulation:
            order = self.simulation.simulate_order(
                market=signal.market,
                outcome=signal.recommended_side,
                side=OrderSide.BUY,
                price=price,
                size=size,
            )
        else:
            order = await self.polymarket.place_order(
                market=signal.market,
                outcome=signal.recommended_side,
                side=OrderSide.BUY,
                price=price,
                size=size,
            )
        
        if order:
            logger.info(
                f"🌦️ EXECUTED | {signal.market.question[:40]}... | "
                f"Buy {signal.recommended_side.value} @ ${price} x {size} | "
                f"Edge: {signal.edge:.1%} (z={signal.edge_zscore:.1f}) | "
                f"Kelly: {signal.kelly_fraction:.1%}"
            )
        
        return order

    def get_signals(self) -> list[WeatherSignal]:
        """Get current signals."""
        return self._signals

    def get_stats(self) -> dict:
        """Get strategy statistics."""
        high_conf = [s for s in self._signals if s.confidence == "HIGH"]
        medium_conf = [s for s in self._signals if s.confidence == "MEDIUM"]
        
        return {
            "total_signals": len(self._signals),
            "high_confidence": len(high_conf),
            "medium_confidence": len(medium_conf),
            "avg_edge": float(sum(s.edge for s in self._signals) / len(self._signals)) if self._signals else 0,
            "avg_zscore": sum(s.edge_zscore for s in self._signals) / len(self._signals) if self._signals else 0,
            "avg_kelly": sum(s.kelly_fraction for s in self._signals) / len(self._signals) if self._signals else 0,
            "total_expected_value": float(sum(s.expected_value for s in self._signals)),
        }
