"""
Tests for the enhanced strategies and new components.
"""

import asyncio
from decimal import Decimal
from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)

# Test the Gaussian probability calculation
from polybot.strategies.weather_v2 import (
    gaussian_cdf,
    get_lead_time_hours,
    WeatherStrategyV2,
    WeatherSignal,
    ForecastConfidence,
)


class TestGaussianCDF:
    """Test the Gaussian CDF implementation."""
    
    def test_cdf_at_mean(self):
        """CDF at mean should be 0.5."""
        result = gaussian_cdf(100, mean=100, std=10)
        assert abs(result - 0.5) < 0.001
    
    def test_cdf_one_std_above(self):
        """One std above mean should be ~84%."""
        result = gaussian_cdf(110, mean=100, std=10)
        assert 0.83 < result < 0.85
    
    def test_cdf_one_std_below(self):
        """One std below mean should be ~16%."""
        result = gaussian_cdf(90, mean=100, std=10)
        assert 0.15 < result < 0.17
    
    def test_cdf_two_std_above(self):
        """Two std above mean should be ~97.7%."""
        result = gaussian_cdf(120, mean=100, std=10)
        assert 0.97 < result < 0.98
    
    def test_cdf_extreme_values(self):
        """Extreme values should approach 0 or 1."""
        low = gaussian_cdf(50, mean=100, std=10)
        high = gaussian_cdf(150, mean=100, std=10)
        
        assert low < 0.001
        assert high > 0.999


class TestLeadTimeCalculation:
    """Test lead time hour calculations."""
    
    def test_tomorrow(self):
        """Tomorrow should be ~24 hours."""
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        hours = get_lead_time_hours(tomorrow)
        assert 20 < hours < 48  # Allow some variance
    
    def test_week_ahead(self):
        """Week ahead should be ~168 hours."""
        next_week = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
        hours = get_lead_time_hours(next_week)
        assert 140 < hours < 180  # Allow wider variance since we use date not datetime


class TestForecastConfidence:
    """Test forecast confidence calibration data."""
    
    def test_rmse_increases_with_lead_time(self):
        """RMSE should increase as lead time increases."""
        rmse = ForecastConfidence.TEMP_RMSE_BY_LEAD
        
        assert rmse[24] < rmse[48]
        assert rmse[48] < rmse[72]
        assert rmse[72] < rmse[120]
        assert rmse[120] < rmse[168]


# Test the crypto strategy Black-Scholes implementation
from polybot.strategies.crypto import (
    black_scholes_probability,
    days_until,
    daily_vol_to_period,
    CryptoStrategy,
    CryptoSignal,
    CRYPTO_VOLATILITY,
)


class TestBlackScholesProbability:
    """Test Black-Scholes probability calculations."""
    
    def test_price_at_threshold(self):
        """When price equals threshold, probability should be ~50%."""
        prob = black_scholes_probability(
            current_price=100000,
            threshold=100000,
            days_to_expiry=30,
            annual_volatility=0.55,
            is_above=True,
        )
        assert 0.45 < prob < 0.55
    
    def test_price_well_above_threshold(self):
        """When price >> threshold, probability should be high."""
        prob = black_scholes_probability(
            current_price=120000,
            threshold=100000,
            days_to_expiry=7,
            annual_volatility=0.55,
            is_above=True,
        )
        assert prob > 0.80
    
    def test_price_well_below_threshold(self):
        """When price << threshold, probability should be low."""
        prob = black_scholes_probability(
            current_price=80000,
            threshold=100000,
            days_to_expiry=7,
            annual_volatility=0.55,
            is_above=True,
        )
        assert prob < 0.20
    
    def test_longer_duration_more_uncertainty(self):
        """Longer duration should pull probability toward 50%."""
        short_term = black_scholes_probability(
            current_price=110000,
            threshold=100000,
            days_to_expiry=1,
            annual_volatility=0.55,
            is_above=True,
        )
        long_term = black_scholes_probability(
            current_price=110000,
            threshold=100000,
            days_to_expiry=30,
            annual_volatility=0.55,
            is_above=True,
        )
        
        # Short term should be more confident (further from 50%)
        assert abs(short_term - 0.5) > abs(long_term - 0.5)
    
    def test_higher_volatility_more_uncertainty(self):
        """Higher volatility should pull probability toward 50%."""
        low_vol = black_scholes_probability(
            current_price=110000,
            threshold=100000,
            days_to_expiry=7,
            annual_volatility=0.30,
            is_above=True,
        )
        high_vol = black_scholes_probability(
            current_price=110000,
            threshold=100000,
            days_to_expiry=7,
            annual_volatility=0.90,
            is_above=True,
        )
        
        # Low vol should be more confident
        assert abs(low_vol - 0.5) > abs(high_vol - 0.5)
    
    def test_is_below_inverts_probability(self):
        """is_above=False should give 1 - is_above result."""
        prob_above = black_scholes_probability(
            current_price=100000,
            threshold=90000,
            days_to_expiry=7,
            annual_volatility=0.55,
            is_above=True,
        )
        prob_below = black_scholes_probability(
            current_price=100000,
            threshold=90000,
            days_to_expiry=7,
            annual_volatility=0.55,
            is_above=False,
        )
        
        assert abs((prob_above + prob_below) - 1.0) < 0.001


class TestDaysUntil:
    """Test days until calculation."""
    
    def test_tomorrow(self):
        """Tomorrow should be ~1 day."""
        tomorrow = datetime.utcnow() + timedelta(days=1)
        days = days_until(tomorrow)
        assert 0.9 < days < 1.1
    
    def test_next_week(self):
        """Next week should be ~7 days."""
        next_week = datetime.utcnow() + timedelta(days=7)
        days = days_until(next_week)
        assert 6.9 < days < 7.1


class TestVolatilityConversion:
    """Test volatility conversion functions."""
    
    def test_annual_to_daily(self):
        """Convert 55% annual vol to 1-day vol."""
        daily_vol = daily_vol_to_period(0.55, 1)
        # sqrt(1/365) * 0.55 ≈ 0.0288
        assert 0.02 < daily_vol < 0.04
    
    def test_annual_to_monthly(self):
        """Convert to 30-day vol."""
        monthly_vol = daily_vol_to_period(0.55, 30)
        # sqrt(30/365) * 0.55 ≈ 0.158
        assert 0.14 < monthly_vol < 0.18


# Test datastore
from polybot.core.datastore import DataStore


class TestDataStore:
    """Test the data persistence layer."""
    
    @pytest.mark.asyncio
    async def test_connect_creates_tables(self, tmp_path):
        """Connecting should create all required tables."""
        db_path = tmp_path / "test.db"
        store = DataStore(str(db_path))
        
        await store.connect()
        
        # Check tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        
        assert "market_snapshots" in tables
        assert "trades" in tables
        assert "signals" in tables
        assert "arb_opportunities" in tables
        assert "daily_performance" in tables
        
        await store.disconnect()
    
    @pytest.mark.asyncio
    async def test_save_and_retrieve_signal(self, tmp_path):
        """Save a signal and retrieve it."""
        db_path = tmp_path / "test.db"
        store = DataStore(str(db_path))
        await store.connect()
        
        # Save a signal
        await store.save_signal(
            signal_id="test_signal_001",
            strategy="weather_v2",
            market_id="market_123",
            market_question="Will it rain in NYC tomorrow?",
            market_price=Decimal("0.45"),
            forecast_price=Decimal("0.72"),
            edge=Decimal("0.27"),
            recommended_side="YES",
            edge_zscore=3.5,
            confidence="HIGH",
            kelly_fraction=0.15,
            reasoning="NOAA says 72% chance",
        )
        
        # Retrieve signals
        performance = await store.get_signal_performance("weather_v2", days=1)
        
        assert "HIGH" in performance
        assert performance["HIGH"]["total_signals"] == 1
        
        await store.disconnect()


# Integration test for weather strategy
class TestWeatherStrategyV2Integration:
    """Integration tests for weather strategy v2."""
    
    def test_extract_city(self):
        """Test city extraction from market questions."""
        # Create mock dependencies
        polymarket = MagicMock()
        noaa = MagicMock()
        
        strategy = WeatherStrategyV2(polymarket, noaa)
        
        assert strategy._extract_city("Will it rain in New York tomorrow?") == "New York"
        assert strategy._extract_city("NYC temperature above 80°F?") == "New York"
        assert strategy._extract_city("Miami high temperature forecast") == "Miami"
        assert strategy._extract_city("Will LA get rain this week?") == "Los Angeles"
        assert strategy._extract_city("Random market question") is None
    
    def test_extract_date(self):
        """Test date extraction from market questions."""
        polymarket = MagicMock()
        noaa = MagicMock()
        
        strategy = WeatherStrategyV2(polymarket, noaa)
        
        # Tomorrow
        result = strategy._extract_date("Will it rain tomorrow?")
        expected = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert result == expected
        
        # Today
        result = strategy._extract_date("Will it rain today?")
        expected = datetime.utcnow().strftime("%Y-%m-%d")
        assert result == expected


# Integration test for crypto strategy
class TestCryptoStrategyIntegration:
    """Integration tests for crypto strategy."""
    
    def test_extract_crypto_symbol(self):
        """Test crypto symbol extraction."""
        polymarket = MagicMock()
        pyth = MagicMock()
        
        strategy = CryptoStrategy(polymarket, pyth)
        
        assert strategy._extract_crypto_symbol("Will Bitcoin reach $100k?") == "BTC/USD"
        assert strategy._extract_crypto_symbol("BTC above $90000 by Feb 15") == "BTC/USD"
        assert strategy._extract_crypto_symbol("Ethereum price prediction") == "ETH/USD"
        assert strategy._extract_crypto_symbol("SOL to hit $200?") == "SOL/USD"
        assert strategy._extract_crypto_symbol("Random market") is None
    
    def test_extract_threshold(self):
        """Test threshold extraction."""
        polymarket = MagicMock()
        pyth = MagicMock()
        
        strategy = CryptoStrategy(polymarket, pyth)
        
        assert strategy._extract_threshold("BTC above $100,000") == 100000
        assert strategy._extract_threshold("Bitcoin to reach $100k") == 100000
        assert strategy._extract_threshold("ETH above $5,000") == 5000
    
    def test_extract_direction(self):
        """Test direction extraction."""
        polymarket = MagicMock()
        pyth = MagicMock()
        
        strategy = CryptoStrategy(polymarket, pyth)
        
        assert strategy._extract_direction("BTC above $100k") is True
        assert strategy._extract_direction("Will Bitcoin exceed $90000") is True
        assert strategy._extract_direction("ETH below $3000") is False
        assert strategy._extract_direction("BTC under $80k") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
