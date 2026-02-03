"""
Weather Arbitrage Strategy

Compares NOAA weather forecasts to prediction market prices.
Identifies mispriced weather markets where the official forecast
strongly disagrees with market pricing.

Example opportunities:
- Market says 40% chance of rain, NOAA says 90% -> Buy YES
- Market says 60% chance of snow, NOAA says 10% -> Buy NO
- Market says high temp > 80°F at 70%, NOAA says 95°F high -> Buy YES
"""

import asyncio
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger

from polybot.connectors.noaa import NOAAConnector, DailyForecast
from polybot.connectors.polymarket import PolymarketConnector
from polybot.core.simulation import SimulationEngine
from polybot.models import Market, Order, OrderSide, OutcomeType


class WeatherOpportunity:
    """A weather-based trading opportunity."""

    def __init__(
        self,
        market: Market,
        market_prob: Decimal,  # What market says (YES price)
        forecast_prob: Decimal,  # What NOAA says
        recommended_side: OutcomeType,
        edge: Decimal,  # Difference between forecast and market
        confidence: str,  # "HIGH", "MEDIUM", "LOW"
        reasoning: str,
    ):
        self.market = market
        self.market_prob = market_prob
        self.forecast_prob = forecast_prob
        self.recommended_side = recommended_side
        self.edge = edge
        self.confidence = confidence
        self.reasoning = reasoning

    @property
    def expected_value(self) -> Decimal:
        """Expected value of the trade."""
        if self.recommended_side == OutcomeType.YES:
            # We buy YES at market_prob, expect to win at forecast_prob
            return self.forecast_prob - self.market_prob
        else:
            # We buy NO at (1 - market_prob), expect to win at (1 - forecast_prob)
            return (1 - self.forecast_prob) - (1 - self.market_prob)

    def __repr__(self) -> str:
        return (
            f"WeatherOpportunity({self.market.question[:40]}... | "
            f"Market: {self.market_prob:.0%} vs Forecast: {self.forecast_prob:.0%} | "
            f"Buy {self.recommended_side.value} | Edge: {self.edge:.1%})"
        )


class WeatherStrategy:
    """
    Weather arbitrage strategy.
    
    Scans weather-related markets and compares to NOAA forecasts.
    """

    # Keywords to identify weather markets
    WEATHER_KEYWORDS = [
        "rain", "snow", "temperature", "degrees", "°f", "°c",
        "weather", "storm", "hurricane", "tornado", "heat",
        "cold", "freeze", "frost", "precipitation", "sunny",
        "cloudy", "wind", "mph", "inches",
    ]

    # Cities to match in market questions
    CITY_PATTERNS = [
        (r"new york|nyc|manhattan", "New York"),
        (r"los angeles|la|hollywood", "Los Angeles"),
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
        (r"san francisco|sf", "San Francisco"),
        (r"tampa", "Tampa"),
    ]

    def __init__(
        self,
        polymarket: PolymarketConnector,
        noaa: NOAAConnector,
        simulation: Optional[SimulationEngine] = None,
        min_edge_percent: Decimal = Decimal("15"),  # 15% edge minimum
    ):
        self.polymarket = polymarket
        self.noaa = noaa
        self.simulation = simulation
        self.min_edge = min_edge_percent / 100
        
        self._running = False
        self._opportunities: list[WeatherOpportunity] = []

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
        
        # Check for "tomorrow"
        if "tomorrow" in question_lower:
            return (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Check for specific date patterns
        # e.g., "January 30", "Jan 30", "1/30", "2026-01-30"
        date_patterns = [
            r"(\d{4}-\d{2}-\d{2})",  # 2026-01-30
            r"(\d{1,2}/\d{1,2})",  # 1/30
            r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})",
            r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})",
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, question_lower)
            if match:
                # For simplicity, assume current year and parse
                try:
                    if "-" in match.group(0):
                        return match.group(0)
                    # Would need more sophisticated parsing here
                except:
                    pass
        
        # Default to tomorrow
        return (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

    async def analyze_rain_market(
        self, market: Market, city: str, date: str
    ) -> Optional[WeatherOpportunity]:
        """Analyze a rain prediction market."""
        try:
            will_rain, prob = await self.noaa.will_it_rain(city, date)
            forecast_prob = Decimal(prob) / 100
            market_prob = market.yes_price
            
            edge = abs(forecast_prob - market_prob)
            
            if edge < self.min_edge:
                return None
            
            # Determine direction
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                reasoning = f"NOAA says {prob}% chance of rain, market only pricing {market_prob:.0%}"
            else:
                recommended = OutcomeType.NO
                reasoning = f"NOAA says only {prob}% chance of rain, market pricing {market_prob:.0%}"
            
            confidence = "HIGH" if edge > Decimal("0.25") else "MEDIUM" if edge > Decimal("0.15") else "LOW"
            
            return WeatherOpportunity(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                recommended_side=recommended,
                edge=edge,
                confidence=confidence,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze rain market: {e}")
            return None

    async def analyze_snow_market(
        self, market: Market, city: str, date: str
    ) -> Optional[WeatherOpportunity]:
        """Analyze a snow prediction market."""
        try:
            will_snow, prob = await self.noaa.will_it_snow(city, date)
            forecast_prob = Decimal(prob) / 100 if will_snow else Decimal("0.05")
            market_prob = market.yes_price
            
            edge = abs(forecast_prob - market_prob)
            
            if edge < self.min_edge:
                return None
            
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                reasoning = f"NOAA forecasts snow ({prob}%), market only pricing {market_prob:.0%}"
            else:
                recommended = OutcomeType.NO
                reasoning = f"NOAA shows no snow expected, market pricing {market_prob:.0%}"
            
            confidence = "HIGH" if edge > Decimal("0.30") else "MEDIUM"
            
            return WeatherOpportunity(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                recommended_side=recommended,
                edge=edge,
                confidence=confidence,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze snow market: {e}")
            return None

    async def analyze_temperature_market(
        self, market: Market, city: str, date: str
    ) -> Optional[WeatherOpportunity]:
        """Analyze a temperature prediction market."""
        try:
            high, low = await self.noaa.get_temperature_forecast(city, date)
            if high is None:
                return None
            
            question_lower = market.question.lower()
            
            # Extract temperature threshold from question
            temp_match = re.search(r"(\d+)\s*(?:degrees|°|f)", question_lower)
            if not temp_match:
                return None
            
            threshold = int(temp_match.group(1))
            
            # Determine if question asks about high or low
            is_high = "high" in question_lower or "above" in question_lower or "over" in question_lower
            is_low = "low" in question_lower or "below" in question_lower or "under" in question_lower
            
            if is_high or (not is_low):
                # High temperature market
                if high >= threshold + 5:
                    forecast_prob = Decimal("0.90")
                elif high >= threshold:
                    forecast_prob = Decimal("0.70")
                elif high >= threshold - 5:
                    forecast_prob = Decimal("0.40")
                else:
                    forecast_prob = Decimal("0.10")
            else:
                # Low temperature market
                if low <= threshold - 5:
                    forecast_prob = Decimal("0.90")
                elif low <= threshold:
                    forecast_prob = Decimal("0.70")
                elif low <= threshold + 5:
                    forecast_prob = Decimal("0.40")
                else:
                    forecast_prob = Decimal("0.10")
            
            market_prob = market.yes_price
            edge = abs(forecast_prob - market_prob)
            
            if edge < self.min_edge:
                return None
            
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                reasoning = f"NOAA forecasts {high}°F high, market underpricing at {market_prob:.0%}"
            else:
                recommended = OutcomeType.NO
                reasoning = f"NOAA forecasts {high}°F high, market overpricing at {market_prob:.0%}"
            
            confidence = "HIGH" if edge > Decimal("0.25") else "MEDIUM"
            
            return WeatherOpportunity(
                market=market,
                market_prob=market_prob,
                forecast_prob=forecast_prob,
                recommended_side=recommended,
                edge=edge,
                confidence=confidence,
                reasoning=reasoning,
            )
            
        except Exception as e:
            logger.warning(f"Failed to analyze temperature market: {e}")
            return None

    async def scan_markets(self) -> list[WeatherOpportunity]:
        """Scan all markets for weather opportunities."""
        logger.info("Scanning for weather arbitrage opportunities...")
        
        opportunities = []
        markets = await self.polymarket.fetch_markets()
        
        weather_markets = [m for m in markets if self._is_weather_market(m)]
        logger.info(f"Found {len(weather_markets)} weather-related markets")
        
        for market in weather_markets:
            city = self._extract_city(market.question)
            if not city:
                continue
            
            date = self._extract_date(market.question)
            question_lower = market.question.lower()
            
            # Analyze based on market type
            opp = None
            
            if "rain" in question_lower or "precipitation" in question_lower:
                opp = await self.analyze_rain_market(market, city, date)
            elif "snow" in question_lower:
                opp = await self.analyze_snow_market(market, city, date)
            elif "temperature" in question_lower or "degrees" in question_lower or "°" in question_lower:
                opp = await self.analyze_temperature_market(market, city, date)
            
            if opp:
                opportunities.append(opp)
                logger.info(f"🌦️ WEATHER OPP: {opp}")
        
        self._opportunities = sorted(opportunities, key=lambda x: x.edge, reverse=True)
        return self._opportunities

    async def execute_opportunity(
        self, opp: WeatherOpportunity, size: Decimal
    ) -> Optional[Order]:
        """Execute a weather opportunity."""
        
        if opp.confidence == "LOW":
            logger.warning("Skipping LOW confidence opportunity")
            return None
        
        price = (
            opp.market.yes_price
            if opp.recommended_side == OutcomeType.YES
            else opp.market.no_price
        )
        
        if self.simulation:
            # Simulated execution
            order = self.simulation.simulate_order(
                market=opp.market,
                outcome=opp.recommended_side,
                side=OrderSide.BUY,
                price=price,
                size=size,
            )
        else:
            # Real execution
            order = await self.polymarket.place_order(
                market=opp.market,
                outcome=opp.recommended_side,
                side=OrderSide.BUY,
                price=price,
                size=size,
            )
        
        if order:
            logger.info(
                f"🌦️ WEATHER TRADE | {opp.market.question[:40]}... | "
                f"Buy {opp.recommended_side.value} @ ${price} x {size} | "
                f"Edge: {opp.edge:.1%}"
            )
        
        return order

    def get_opportunities(self) -> list[WeatherOpportunity]:
        """Get current opportunities."""
        return self._opportunities

    def get_stats(self) -> dict:
        """Get strategy stats."""
        return {
            "weather_markets_found": len([o for o in self._opportunities]),
            "high_confidence_opps": len([o for o in self._opportunities if o.confidence == "HIGH"]),
            "medium_confidence_opps": len([o for o in self._opportunities if o.confidence == "MEDIUM"]),
            "avg_edge": float(sum(o.edge for o in self._opportunities) / len(self._opportunities)) if self._opportunities else 0,
        }
