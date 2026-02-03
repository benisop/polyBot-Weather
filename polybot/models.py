"""Data models for markets, orders, and positions."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    """Order side enum."""

    BUY = "BUY"
    SELL = "SELL"


class OutcomeType(str, Enum):
    """Binary outcome type."""

    YES = "YES"
    NO = "NO"


class OrderStatus(str, Enum):
    """Order status enum."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PositionStatus(str, Enum):
    """Position status enum."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"


class Market(BaseModel):
    """Prediction market model."""

    id: str = Field(..., description="Unique market identifier")
    condition_id: str = Field(..., description="Condition ID for the market")
    question: str = Field(..., description="Market question")
    description: Optional[str] = None

    # Token IDs for YES/NO outcomes
    yes_token_id: str
    no_token_id: str

    # Current prices (0-1 representing probability)
    yes_price: Decimal = Field(ge=0, le=1)
    no_price: Decimal = Field(ge=0, le=1)

    # Market metadata
    volume: Decimal = Field(default=Decimal("0"))
    liquidity: Decimal = Field(default=Decimal("0"))
    end_date: Optional[datetime] = None
    is_active: bool = True

    # Orderbook depth (best bid/ask sizes)
    yes_bid_size: Decimal = Field(default=Decimal("0"))
    yes_ask_size: Decimal = Field(default=Decimal("0"))
    no_bid_size: Decimal = Field(default=Decimal("0"))
    no_ask_size: Decimal = Field(default=Decimal("0"))

    @property
    def arb_spread(self) -> Decimal:
        """Calculate arbitrage spread: profit if YES + NO < 1."""
        total = self.yes_price + self.no_price
        return Decimal("1") - total if total < Decimal("1") else Decimal("0")

    @property
    def is_arbitrageable(self) -> bool:
        """Check if market has arbitrage opportunity (> 1% profit)."""
        return self.arb_spread > Decimal("0.01")


class Order(BaseModel):
    """Order model."""

    id: Optional[str] = None
    market_id: str
    token_id: str
    side: OrderSide
    outcome: OutcomeType
    price: Decimal = Field(ge=0, le=1)
    size: Decimal = Field(gt=0)
    filled_size: Decimal = Field(default=Decimal("0"))
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    @property
    def remaining_size(self) -> Decimal:
        """Get unfilled size."""
        return self.size - self.filled_size


class Position(BaseModel):
    """Position model tracking holdings in a market."""

    id: str
    market_id: str
    market_question: str
    outcome: OutcomeType
    token_id: str

    # Position details
    size: Decimal = Field(ge=0)
    avg_entry_price: Decimal = Field(ge=0, le=1)
    current_price: Decimal = Field(ge=0, le=1)
    status: PositionStatus = PositionStatus.OPEN

    # Timestamps
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None

    @property
    def unrealized_pnl(self) -> Decimal:
        """Calculate unrealized P&L."""
        return (self.current_price - self.avg_entry_price) * self.size

    @property
    def unrealized_pnl_percent(self) -> Decimal:
        """Calculate unrealized P&L as percentage."""
        if self.avg_entry_price == 0:
            return Decimal("0")
        return (self.unrealized_pnl / (self.avg_entry_price * self.size)) * 100


class ArbOpportunity(BaseModel):
    """Arbitrage opportunity model."""

    market: Market
    yes_buy_price: Decimal
    no_buy_price: Decimal
    total_cost: Decimal
    guaranteed_payout: Decimal = Decimal("1")  # Binary markets pay $1 on win
    gross_profit: Decimal
    estimated_fees: Decimal
    net_profit: Decimal
    net_profit_percent: Decimal
    max_size: Decimal  # Limited by orderbook depth
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_profitable(self) -> bool:
        """Check if opportunity is profitable after fees."""
        return self.net_profit > Decimal("0")


class PortfolioSnapshot(BaseModel):
    """Portfolio state at a point in time."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total_capital: Decimal
    available_capital: Decimal
    deployed_capital: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    open_positions: int
    win_rate: Decimal = Field(default=Decimal("0"))
    total_trades: int = 0
    winning_trades: int = 0
