"""
Risk Management Engine

Controls:
- Max position sizing (5% per trade)
- Slippage protection
- Circuit breakers
- Daily loss limits
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger
from pydantic import BaseModel


class RiskLimits(BaseModel):
    """Risk limit configuration."""

    max_position_percent: Decimal = Decimal("5.0")
    max_slippage_percent: Decimal = Decimal("2.0")
    max_daily_loss_percent: Decimal = Decimal("10.0")
    max_open_positions: int = 20
    min_profit_threshold: Decimal = Decimal("1.5")
    min_liquidity: Decimal = Decimal("100")  # Minimum $100 liquidity


class RiskState(BaseModel):
    """Current risk state."""

    daily_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    deployed_capital: Decimal = Decimal("0")
    circuit_breaker_triggered: bool = False
    last_reset: datetime = datetime.utcnow()


class RiskManager:
    """Risk management engine."""

    def __init__(self, total_capital: Decimal, limits: Optional[RiskLimits] = None):
        self.total_capital = total_capital
        self.limits = limits or RiskLimits()
        self.state = RiskState()

        logger.info(
            f"Risk Manager initialized | Capital: ${total_capital} | "
            f"Max per trade: {self.limits.max_position_percent}%"
        )

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed."""
        # Circuit breaker check
        if self.state.circuit_breaker_triggered:
            return False, "Circuit breaker triggered"

        # Daily loss check
        if self._check_daily_loss():
            self.state.circuit_breaker_triggered = True
            return False, f"Daily loss limit exceeded: ${self.state.daily_pnl}"

        # Position count check
        if self.state.open_positions >= self.limits.max_open_positions:
            return False, f"Max positions reached: {self.state.open_positions}"

        return True, "OK"

    def validate_trade(
        self,
        size: Decimal,
        expected_profit_percent: Decimal,
        liquidity: Decimal,
    ) -> tuple[bool, str, Decimal]:
        """
        Validate a proposed trade.

        Returns: (allowed, reason, adjusted_size)
        """
        can, reason = self.can_trade()
        if not can:
            return False, reason, Decimal("0")

        # Minimum profit check
        if expected_profit_percent < self.limits.min_profit_threshold:
            return (
                False,
                f"Profit {expected_profit_percent}% below threshold {self.limits.min_profit_threshold}%",
                Decimal("0"),
            )

        # Liquidity check
        if liquidity < self.limits.min_liquidity:
            return False, f"Liquidity ${liquidity} below minimum ${self.limits.min_liquidity}", Decimal("0")

        # Size limits
        max_size = self._calculate_max_size()
        adjusted_size = min(size, max_size, liquidity)

        if adjusted_size < Decimal("1"):
            return False, "Adjusted size below $1 minimum", Decimal("0")

        return True, "OK", adjusted_size

    def _calculate_max_size(self) -> Decimal:
        """Calculate maximum allowed position size."""
        # Percentage of capital
        percent_limit = self.total_capital * (self.limits.max_position_percent / 100)

        # Available capital
        available = self.total_capital - self.state.deployed_capital

        return min(percent_limit, available)

    def _check_daily_loss(self) -> bool:
        """Check if daily loss limit exceeded."""
        # Reset at midnight
        now = datetime.utcnow()
        if now.date() > self.state.last_reset.date():
            self.state.daily_pnl = Decimal("0")
            self.state.last_reset = now
            self.state.circuit_breaker_triggered = False

        loss_limit = self.total_capital * (self.limits.max_daily_loss_percent / 100)
        return self.state.daily_pnl < -loss_limit

    def record_trade_open(self, size: Decimal) -> None:
        """Record a new position opened."""
        self.state.open_positions += 1
        self.state.deployed_capital += size
        logger.debug(f"Position opened | Size: ${size} | Total deployed: ${self.state.deployed_capital}")

    def record_trade_close(self, size: Decimal, pnl: Decimal) -> None:
        """Record a position closed."""
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.deployed_capital = max(Decimal("0"), self.state.deployed_capital - size)
        self.state.daily_pnl += pnl

        logger.debug(f"Position closed | PnL: ${pnl} | Daily PnL: ${self.state.daily_pnl}")

    def check_slippage(
        self, expected_price: Decimal, actual_price: Decimal
    ) -> tuple[bool, Decimal]:
        """
        Check if slippage is acceptable.

        Returns: (acceptable, slippage_percent)
        """
        if expected_price == 0:
            return False, Decimal("100")

        slippage = abs(actual_price - expected_price) / expected_price * 100

        acceptable = slippage <= self.limits.max_slippage_percent
        if not acceptable:
            logger.warning(
                f"Slippage rejected: {slippage:.2f}% > {self.limits.max_slippage_percent}%"
            )

        return acceptable, slippage

    def reset_circuit_breaker(self) -> None:
        """Manually reset circuit breaker."""
        self.state.circuit_breaker_triggered = False
        logger.info("Circuit breaker reset")

    def update_capital(self, new_capital: Decimal) -> None:
        """Update total capital."""
        self.total_capital = new_capital
        logger.info(f"Capital updated to ${new_capital}")

    def get_status(self) -> dict:
        """Get current risk status."""
        return {
            "can_trade": self.can_trade()[0],
            "reason": self.can_trade()[1],
            "total_capital": float(self.total_capital),
            "deployed_capital": float(self.state.deployed_capital),
            "available_capital": float(self.total_capital - self.state.deployed_capital),
            "open_positions": self.state.open_positions,
            "daily_pnl": float(self.state.daily_pnl),
            "circuit_breaker": self.state.circuit_breaker_triggered,
            "max_position_size": float(self._calculate_max_size()),
        }
