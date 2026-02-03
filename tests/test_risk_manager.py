"""Tests for risk manager."""

from decimal import Decimal

import pytest

from polybot.core.risk_manager import RiskManager, RiskLimits


def test_can_trade_default():
    """Test trading allowed by default."""
    rm = RiskManager(total_capital=Decimal("1000"))
    can, reason = rm.can_trade()
    assert can is True
    assert reason == "OK"


def test_max_positions():
    """Test max positions limit."""
    rm = RiskManager(
        total_capital=Decimal("1000"),
        limits=RiskLimits(max_open_positions=2),
    )

    rm.record_trade_open(Decimal("50"))
    rm.record_trade_open(Decimal("50"))

    can, reason = rm.can_trade()
    assert can is False
    assert "Max positions" in reason


def test_circuit_breaker():
    """Test circuit breaker trigger."""
    rm = RiskManager(total_capital=Decimal("1000"))
    rm.state.circuit_breaker_triggered = True

    can, reason = rm.can_trade()
    assert can is False
    assert "Circuit breaker" in reason


def test_validate_trade_profit_threshold():
    """Test minimum profit threshold."""
    rm = RiskManager(
        total_capital=Decimal("1000"),
        limits=RiskLimits(min_profit_threshold=Decimal("2.0")),
    )

    allowed, reason, size = rm.validate_trade(
        size=Decimal("50"),
        expected_profit_percent=Decimal("1.5"),  # Below threshold
        liquidity=Decimal("1000"),
    )

    assert allowed is False
    assert "below threshold" in reason


def test_slippage_check():
    """Test slippage protection."""
    rm = RiskManager(total_capital=Decimal("1000"))

    # Acceptable slippage
    ok, slip = rm.check_slippage(Decimal("0.50"), Decimal("0.51"))
    assert ok is True
    assert slip == Decimal("2")

    # Unacceptable slippage
    ok, slip = rm.check_slippage(Decimal("0.50"), Decimal("0.55"))
    assert ok is False
