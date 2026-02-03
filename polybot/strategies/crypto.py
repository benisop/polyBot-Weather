"""
Crypto Price Prediction Strategy

Trades daily crypto price prediction markets (e.g., "Will BTC be above $100k on Feb 15?")
using real-time Pyth oracle prices and statistical distance analysis.

Design for latency tolerance:
- Targets daily+ markets (not 15-minute)
- Edge comes from better probability estimation, not speed
- Uses current price position relative to threshold + volatility
"""

import asyncio
import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List
from dataclasses import dataclass
import re

from loguru import logger

from polybot.connectors.pyth import PythConnector
from polybot.connectors.polymarket import PolymarketConnector
from polybot.core.simulation import SimulationEngine
from polybot.models import Market, Order, OrderSide, OutcomeType


# Historical daily volatility (annualized) for major cryptos
# Used for probability calculations
CRYPTO_VOLATILITY = {
    "BTC": 0.55,   # ~55% annualized volatility
    "ETH": 0.70,   # ~70%
    "SOL": 0.95,   # ~95%
    "DOGE": 1.10,  # ~110%
    "XRP": 0.85,   # ~85%
}

# Map Polymarket question patterns to Pyth symbols
CRYPTO_PATTERNS = [
    (r"bitcoin|btc", "BTC/USD"),
    (r"ethereum|eth", "ETH/USD"),
    (r"solana|sol", "SOL/USD"),
    (r"dogecoin|doge", "DOGE/USD"),
    (r"xrp|ripple", "XRP/USD"),
]


def days_until(target_date: datetime) -> float:
    """Calculate days from now until target date."""
    now = datetime.utcnow()
    delta = target_date - now
    return max(0.1, delta.total_seconds() / 86400)


def daily_vol_to_period(annualized_vol: float, days: float) -> float:
    """Convert annualized volatility to period volatility."""
    # σ_period = σ_annual * sqrt(days / 365)
    return annualized_vol * math.sqrt(days / 365)


def black_scholes_probability(
    current_price: float,
    threshold: float,
    days_to_expiry: float,
    annual_volatility: float,
    is_above: bool = True,
) -> float:
    """
    Calculate probability that price will be above/below threshold at expiry.
    
    Uses simplified Black-Scholes model (risk-neutral, no drift assumption).
    
    P(S_T > K) = N(d2) where d2 = [ln(S/K)] / [σ * sqrt(T)]
    
    For prediction markets, we use a zero-drift model (no risk-free rate)
    since we're estimating real-world probability, not risk-neutral.
    """
    if current_price <= 0 or threshold <= 0:
        return 0.5
    
    T = days_to_expiry / 365  # Time in years
    sigma = annual_volatility
    
    # Calculate d2 (simplified for zero drift)
    # d2 = ln(S/K) / (σ * sqrt(T))
    ln_ratio = math.log(current_price / threshold)
    vol_sqrt_t = sigma * math.sqrt(T)
    
    if vol_sqrt_t == 0:
        # No time or volatility - just compare prices
        return 1.0 if current_price > threshold else 0.0
    
    d2 = ln_ratio / vol_sqrt_t
    
    # N(d2) = probability price ends above strike
    prob_above = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
    
    if is_above:
        return prob_above
    else:
        return 1 - prob_above


@dataclass
class CryptoSignal:
    """A crypto price prediction trading signal."""
    
    market: Market
    crypto_symbol: str
    current_price: Decimal
    threshold_price: Decimal
    days_to_expiry: float
    
    market_prob: Decimal  # What market says (YES price)
    forecast_prob: Decimal  # Our calculated probability
    forecast_std: Decimal  # Uncertainty in our estimate
    
    recommended_side: OutcomeType
    edge: Decimal
    edge_zscore: float
    confidence: str
    kelly_fraction: float
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
        
        return win_prob - cost
    
    @property
    def is_tradeable(self) -> bool:
        """Check if signal meets trading criteria."""
        return self.edge_zscore >= 1.5 and self.edge >= Decimal("0.08")
    
    def __repr__(self) -> str:
        return (
            f"CryptoSignal({self.crypto_symbol} | "
            f"${self.current_price:,.0f} vs ${self.threshold_price:,.0f} | "
            f"{self.days_to_expiry:.1f}d | "
            f"Market: {self.market_prob:.0%} vs Model: {self.forecast_prob:.0%} | "
            f"Edge: {self.edge:.1%} (z={self.edge_zscore:.1f}))"
        )


class CryptoStrategy:
    """
    Crypto price prediction strategy for daily+ markets.
    
    Uses Black-Scholes probability model with real-time Pyth prices
    to find mispriced crypto threshold markets.
    """

    CRYPTO_KEYWORDS = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
        "dogecoin", "doge", "xrp", "ripple", "crypto", "price",
        "$", "above", "below", "reach", "hit", "exceed",
    ]

    def __init__(
        self,
        polymarket: PolymarketConnector,
        pyth: PythConnector,
        simulation: Optional[SimulationEngine] = None,
        min_edge_percent: Decimal = Decimal("8"),  # 8% edge minimum
        min_zscore: float = 1.5,
        min_days_to_expiry: float = 0.5,  # At least 12 hours
        max_days_to_expiry: float = 30,  # Don't trade >30 day markets
    ):
        self.polymarket = polymarket
        self.pyth = pyth
        self.simulation = simulation
        self.min_edge = min_edge_percent / 100
        self.min_zscore = min_zscore
        self.min_days = min_days_to_expiry
        self.max_days = max_days_to_expiry
        
        self._signals: list[CryptoSignal] = []
        self._running = False

    def _is_crypto_market(self, market: Market) -> bool:
        """Check if market is a crypto price prediction."""
        question_lower = market.question.lower()
        return any(kw in question_lower for kw in self.CRYPTO_KEYWORDS)

    def _extract_crypto_symbol(self, question: str) -> Optional[str]:
        """Extract crypto symbol from question."""
        question_lower = question.lower()
        for pattern, pyth_symbol in CRYPTO_PATTERNS:
            if re.search(pattern, question_lower):
                return pyth_symbol
        return None

    def _extract_threshold(self, question: str) -> Optional[float]:
        """Extract price threshold from question."""
        question_lower = question.lower()
        
        # Match patterns like "$100k", "$100,000", "100000 dollars"
        patterns = [
            (r"\$([0-9,]+(?:\.[0-9]+)?)\s*k\b", True),   # $100k (with k suffix)
            (r"\$([0-9,]+(?:\.[0-9]+)?)\b", False),      # $100,000 (no k)
            (r"([0-9,]+(?:\.[0-9]+)?)\s*(?:dollars|usd)", False),  # 100000 dollars
        ]
        
        for pattern, has_k_suffix in patterns:
            match = re.search(pattern, question_lower)
            if match:
                value_str = match.group(1).replace(",", "")
                value = float(value_str)
                
                # Apply k multiplier if pattern includes k
                if has_k_suffix:
                    value *= 1000
                elif value < 1000 and "btc" in question_lower:
                    # Small values for BTC are likely meant as thousands
                    value *= 1000
                
                return value
        
        return None

    def _extract_expiry_date(self, question: str) -> Optional[datetime]:
        """Extract expiry date from question."""
        question_lower = question.lower()
        now = datetime.utcnow()
        
        # Check for explicit dates
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, 
            "oct": 10, "nov": 11, "dec": 12,
        }
        
        for month_name, month_num in month_names.items():
            # Pattern: "February 15" or "Feb 15, 2026"
            pattern = rf"{month_name}\s+(\d{{1,2}})(?:,?\s*(\d{{4}})?)?"
            match = re.search(pattern, question_lower)
            if match:
                day = int(match.group(1))
                year = int(match.group(2)) if match.group(2) else now.year
                
                # If date has passed this year, assume next year
                target = datetime(year, month_num, day, 23, 59, 59)
                if target < now:
                    target = datetime(year + 1, month_num, day, 23, 59, 59)
                
                return target
        
        # Check for relative dates
        if "end of month" in question_lower:
            next_month = (now.month % 12) + 1
            year = now.year if next_month > now.month else now.year + 1
            return datetime(year, next_month, 1) - timedelta(days=1)
        
        if "end of year" in question_lower or "december 31" in question_lower:
            return datetime(now.year, 12, 31, 23, 59, 59)
        
        if "tomorrow" in question_lower:
            return now + timedelta(days=1)
        
        if "next week" in question_lower:
            return now + timedelta(days=7)
        
        # Default: use market end date if available
        return None

    def _extract_direction(self, question: str) -> bool:
        """Determine if question asks about price being ABOVE threshold."""
        question_lower = question.lower()
        
        above_keywords = ["above", "over", "exceed", "reach", "hit", "at least", "higher than", ">"]
        below_keywords = ["below", "under", "less than", "lower than", "fall", "<"]
        
        for kw in above_keywords:
            if kw in question_lower:
                return True
        
        for kw in below_keywords:
            if kw in question_lower:
                return False
        
        # Default to above
        return True

    def _calculate_kelly(
        self,
        win_prob: Decimal,
        cost: Decimal,
    ) -> float:
        """Calculate Kelly criterion bet sizing."""
        p = float(win_prob)
        q = 1 - p
        
        if cost <= 0 or cost >= 1:
            return 0.0
        
        b = (1.0 / float(cost)) - 1
        
        if b <= 0:
            return 0.0
        
        kelly = (p * b - q) / b
        return max(0, kelly * 0.20)  # 1/5 Kelly for safety

    async def analyze_market(
        self,
        market: Market,
    ) -> Optional[CryptoSignal]:
        """Analyze a crypto price prediction market."""
        try:
            # Extract market parameters
            pyth_symbol = self._extract_crypto_symbol(market.question)
            if not pyth_symbol:
                return None
            
            threshold = self._extract_threshold(market.question)
            if not threshold:
                logger.debug(f"Could not extract threshold from: {market.question[:50]}")
                return None
            
            expiry_date = self._extract_expiry_date(market.question)
            if not expiry_date:
                # Use market end date if available
                expiry_date = market.end_date
            
            if not expiry_date:
                return None
            
            days_to_expiry = days_until(expiry_date)
            
            # Filter by expiry window
            if days_to_expiry < self.min_days or days_to_expiry > self.max_days:
                return None
            
            is_above = self._extract_direction(market.question)
            
            # Get current price from Pyth
            current_price = self.pyth.get_cached_price(pyth_symbol)
            if not current_price:
                price_data = await self.pyth.get_price(self.pyth.FEEDS.get(pyth_symbol, ""))
                if price_data:
                    current_price = price_data.price_adjusted
            
            if not current_price:
                logger.debug(f"No price data for {pyth_symbol}")
                return None
            
            # Get volatility for this crypto
            crypto_base = pyth_symbol.split("/")[0]  # "BTC/USD" -> "BTC"
            annual_vol = CRYPTO_VOLATILITY.get(crypto_base, 0.70)
            
            # Calculate probability using Black-Scholes
            prob = black_scholes_probability(
                current_price=float(current_price),
                threshold=threshold,
                days_to_expiry=days_to_expiry,
                annual_volatility=annual_vol,
                is_above=is_above,
            )
            
            forecast_prob = Decimal(str(round(prob, 4)))
            
            # Estimate uncertainty in our probability
            # Higher uncertainty for longer duration and higher volatility
            period_vol = daily_vol_to_period(annual_vol, days_to_expiry)
            prob_std = Decimal(str(min(0.20, period_vol * 0.3)))
            
            market_prob = market.yes_price
            edge = abs(forecast_prob - market_prob)
            
            # Calculate z-score
            combined_std = float(prob_std) + 0.05
            zscore = float(edge) / combined_std if combined_std > 0 else 0
            
            if edge < self.min_edge or zscore < self.min_zscore:
                return None
            
            # Determine trade direction
            if forecast_prob > market_prob:
                recommended = OutcomeType.YES
                cost = market_prob
                reasoning = (
                    f"BS model: {pyth_symbol} @ ${current_price:,.0f}, "
                    f"P(>{threshold:,.0f} in {days_to_expiry:.1f}d) = {forecast_prob:.0%}, "
                    f"market pricing {market_prob:.0%}"
                )
            else:
                recommended = OutcomeType.NO
                cost = Decimal("1") - market_prob
                reasoning = (
                    f"BS model: {pyth_symbol} @ ${current_price:,.0f}, "
                    f"P(>{threshold:,.0f} in {days_to_expiry:.1f}d) = {forecast_prob:.0%}, "
                    f"market overpricing at {market_prob:.0%}"
                )
            
            # Kelly sizing
            win_prob = forecast_prob if recommended == OutcomeType.YES else (Decimal("1") - forecast_prob)
            kelly = self._calculate_kelly(win_prob, cost)
            
            # Confidence level
            if zscore >= 2.5 and edge >= Decimal("0.15"):
                confidence = "HIGH"
            elif zscore >= 1.8 and edge >= Decimal("0.10"):
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            return CryptoSignal(
                market=market,
                crypto_symbol=pyth_symbol,
                current_price=current_price,
                threshold_price=Decimal(str(threshold)),
                days_to_expiry=days_to_expiry,
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
            logger.warning(f"Failed to analyze market: {e}")
            return None

    async def scan_markets(self) -> list[CryptoSignal]:
        """Scan all markets for crypto price prediction opportunities."""
        logger.info("₿ Scanning for crypto price prediction opportunities...")
        
        # Make sure we have fresh prices
        await self.pyth.get_crypto_prices()
        
        signals = []
        markets = await self.polymarket.fetch_markets()
        
        crypto_markets = [m for m in markets if self._is_crypto_market(m)]
        logger.info(f"Found {len(crypto_markets)} crypto-related markets")
        
        for market in crypto_markets:
            signal = await self.analyze_market(market)
            
            if signal and signal.is_tradeable:
                signals.append(signal)
                logger.info(f"₿ SIGNAL: {signal}")
            elif signal:
                logger.debug(f"Weak signal (z={signal.edge_zscore:.1f}): {market.question[:50]}")
        
        self._signals = sorted(signals, key=lambda x: (x.confidence == "HIGH", x.edge_zscore), reverse=True)
        logger.info(f"Found {len(self._signals)} tradeable crypto signals")
        return self._signals

    async def execute_signal(
        self,
        signal: CryptoSignal,
        capital: Decimal,
    ) -> Optional[Order]:
        """Execute a crypto signal with Kelly-optimal sizing."""
        if signal.confidence == "LOW":
            logger.warning("Skipping LOW confidence signal")
            return None
        
        # Size based on Kelly fraction
        size = capital * Decimal(str(signal.kelly_fraction))
        size = max(Decimal("1"), min(size, Decimal("100")))
        
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
                f"₿ EXECUTED | {signal.crypto_symbol} | "
                f"Buy {signal.recommended_side.value} @ ${price} x {size} | "
                f"Edge: {signal.edge:.1%} (z={signal.edge_zscore:.1f})"
            )
        
        return order

    def get_signals(self) -> list[CryptoSignal]:
        """Get current signals."""
        return self._signals

    def get_stats(self) -> dict:
        """Get strategy statistics."""
        high_conf = [s for s in self._signals if s.confidence == "HIGH"]
        medium_conf = [s for s in self._signals if s.confidence == "MEDIUM"]
        
        by_crypto = {}
        for s in self._signals:
            crypto = s.crypto_symbol
            if crypto not in by_crypto:
                by_crypto[crypto] = {"count": 0, "avg_edge": 0}
            by_crypto[crypto]["count"] += 1
            by_crypto[crypto]["avg_edge"] += float(s.edge)
        
        for crypto in by_crypto:
            by_crypto[crypto]["avg_edge"] /= by_crypto[crypto]["count"]
        
        return {
            "total_signals": len(self._signals),
            "high_confidence": len(high_conf),
            "medium_confidence": len(medium_conf),
            "avg_edge": float(sum(s.edge for s in self._signals) / len(self._signals)) if self._signals else 0,
            "avg_zscore": sum(s.edge_zscore for s in self._signals) / len(self._signals) if self._signals else 0,
            "by_crypto": by_crypto,
        }
