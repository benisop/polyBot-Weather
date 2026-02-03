"""Tests for models."""

from decimal import Decimal

import pytest

from polybot.models import Market, ArbOpportunity


def test_market_arb_spread():
    """Test arbitrage spread calculation."""
    market = Market(
        id="test-123",
        condition_id="cond-123",
        question="Test market?",
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_price=Decimal("0.52"),
        no_price=Decimal("0.45"),
    )

    # Total = 0.97, spread = 0.03
    assert market.arb_spread == Decimal("0.03")
    assert market.is_arbitrageable is True


def test_market_no_arb():
    """Test no arbitrage when prices sum to >= 1."""
    market = Market(
        id="test-456",
        condition_id="cond-456",
        question="Fair market?",
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_price=Decimal("0.55"),
        no_price=Decimal("0.46"),
    )

    # Total = 1.01, no arb
    assert market.arb_spread == Decimal("0")
    assert market.is_arbitrageable is False


def test_arb_opportunity_profit():
    """Test arbitrage opportunity profit calculation."""
    market = Market(
        id="test-789",
        condition_id="cond-789",
        question="Profitable?",
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.47"),
    )

    opp = ArbOpportunity(
        market=market,
        yes_buy_price=Decimal("0.50"),
        no_buy_price=Decimal("0.47"),
        total_cost=Decimal("0.97"),
        gross_profit=Decimal("0.03"),
        estimated_fees=Decimal("0.0006"),  # 2% of profit
        net_profit=Decimal("0.0294"),
        net_profit_percent=Decimal("3.03"),
        max_size=Decimal("100"),
    )

    assert opp.is_profitable is True
    assert opp.net_profit > Decimal("0")
