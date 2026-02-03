"""
Simulation Engine - Paper Trading Mode

Allows testing strategies without real money:
- Virtual wallet with configurable starting balance
- Tracks all trades, positions, and P&L
- Simulates order fills with configurable slippage
- Exports trade history for analysis
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from polybot.models import (
    ArbOpportunity,
    Market,
    Order,
    OrderSide,
    OrderStatus,
    OutcomeType,
    Position,
    PositionStatus,
)


class SimulatedTrade(BaseModel):
    """Record of a simulated trade."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    market_id: str
    market_question: str
    outcome: OutcomeType
    side: OrderSide
    price: Decimal
    size: Decimal
    fee: Decimal
    total_cost: Decimal  # price * size + fee


class SimulatedPosition(BaseModel):
    """Simulated position in a market."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    market_id: str
    market_question: str
    outcome: OutcomeType
    size: Decimal
    avg_price: Decimal
    current_price: Decimal
    opened_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def unrealized_pnl(self) -> Decimal:
        """Unrealized P&L based on current price."""
        return (self.current_price - self.avg_price) * self.size

    @property
    def market_value(self) -> Decimal:
        """Current market value of position."""
        return self.current_price * self.size


class SimulationState(BaseModel):
    """Complete simulation state."""

    started_at: datetime = Field(default_factory=datetime.utcnow)
    initial_balance: Decimal
    current_balance: Decimal
    trades: list[SimulatedTrade] = Field(default_factory=list)
    positions: dict[str, SimulatedPosition] = Field(default_factory=dict)  # key: market_id + outcome
    
    # Arb tracking
    arb_pairs: list[dict] = Field(default_factory=list)  # Track YES+NO pairs

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def total_fees_paid(self) -> Decimal:
        return sum(t.fee for t in self.trades)

    @property
    def realized_pnl(self) -> Decimal:
        return self.current_balance - self.initial_balance

    @property
    def unrealized_pnl(self) -> Decimal:
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def portfolio_value(self) -> Decimal:
        """Total value: cash + positions."""
        positions_value = sum(p.market_value for p in self.positions.values())
        return self.current_balance + positions_value


class SimulationEngine:
    """
    Paper trading simulation engine.
    
    Wraps the real connector to intercept trades and simulate execution.
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("1000"),
        fee_percent: Decimal = Decimal("0.02"),  # 2% fee
        slippage_percent: Decimal = Decimal("0.5"),  # 0.5% slippage
    ):
        self.fee_percent = fee_percent
        self.slippage_percent = slippage_percent
        self.state = SimulationState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
        )
        
        logger.info(
            f"🎮 SIMULATION MODE | Starting balance: ${initial_balance} | "
            f"Fee: {fee_percent*100}% | Slippage: {slippage_percent*100}%"
        )

    def simulate_order(
        self,
        market: Market,
        outcome: OutcomeType,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
    ) -> Order:
        """Simulate order execution."""
        
        # Apply slippage (worse price for us)
        slippage = price * (self.slippage_percent / 100)
        if side == OrderSide.BUY:
            exec_price = price + slippage  # Pay more
        else:
            exec_price = price - slippage  # Receive less
        
        exec_price = min(max(exec_price, Decimal("0.01")), Decimal("0.99"))
        
        # Calculate fee
        fee = size * exec_price * self.fee_percent
        total_cost = size * exec_price + fee
        
        # Check balance
        if side == OrderSide.BUY and total_cost > self.state.current_balance:
            logger.warning(f"Insufficient balance: need ${total_cost}, have ${self.state.current_balance}")
            return Order(
                market_id=market.id,
                token_id=market.yes_token_id if outcome == OutcomeType.YES else market.no_token_id,
                side=side,
                outcome=outcome,
                price=exec_price,
                size=size,
                status=OrderStatus.FAILED,
            )
        
        # Execute trade
        trade = SimulatedTrade(
            market_id=market.id,
            market_question=market.question[:50],
            outcome=outcome,
            side=side,
            price=exec_price,
            size=size,
            fee=fee,
            total_cost=total_cost,
        )
        self.state.trades.append(trade)
        
        # Update balance
        if side == OrderSide.BUY:
            self.state.current_balance -= total_cost
        else:
            self.state.current_balance += (size * exec_price) - fee
        
        # Update position
        pos_key = f"{market.id}_{outcome.value}"
        if pos_key in self.state.positions:
            pos = self.state.positions[pos_key]
            if side == OrderSide.BUY:
                # Add to position
                new_size = pos.size + size
                pos.avg_price = ((pos.avg_price * pos.size) + (exec_price * size)) / new_size
                pos.size = new_size
            else:
                # Reduce position
                pos.size -= size
                if pos.size <= 0:
                    del self.state.positions[pos_key]
        elif side == OrderSide.BUY:
            # New position
            self.state.positions[pos_key] = SimulatedPosition(
                market_id=market.id,
                market_question=market.question[:50],
                outcome=outcome,
                size=size,
                avg_price=exec_price,
                current_price=exec_price,
            )
        
        order = Order(
            id=trade.id,
            market_id=market.id,
            token_id=market.yes_token_id if outcome == OutcomeType.YES else market.no_token_id,
            side=side,
            outcome=outcome,
            price=exec_price,
            size=size,
            filled_size=size,
            status=OrderStatus.FILLED,
        )
        
        logger.info(
            f"📝 SIM TRADE | {side.value} {size} {outcome.value} @ ${exec_price:.4f} | "
            f"Fee: ${fee:.4f} | Balance: ${self.state.current_balance:.2f}"
        )
        
        return order

    def simulate_arbitrage(
        self,
        opportunity: ArbOpportunity,
        size: Decimal,
    ) -> tuple[Order, Order]:
        """Simulate arbitrage execution (buy both YES and NO)."""
        
        market = opportunity.market
        
        # Execute both sides
        yes_order = self.simulate_order(
            market=market,
            outcome=OutcomeType.YES,
            side=OrderSide.BUY,
            price=opportunity.yes_buy_price,
            size=size,
        )
        
        no_order = self.simulate_order(
            market=market,
            outcome=OutcomeType.NO,
            side=OrderSide.BUY,
            price=opportunity.no_buy_price,
            size=size,
        )
        
        if yes_order.status == OrderStatus.FILLED and no_order.status == OrderStatus.FILLED:
            # Track as arb pair
            total_cost = (yes_order.price + no_order.price) * size
            expected_profit = size - total_cost  # Settles to $1 * size
            
            self.state.arb_pairs.append({
                "market_id": market.id,
                "question": market.question[:50],
                "size": float(size),
                "total_cost": float(total_cost),
                "expected_profit": float(expected_profit),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            logger.info(
                f"🎯 SIM ARB EXECUTED | {market.question[:40]}... | "
                f"Cost: ${total_cost:.4f} | Expected profit: ${expected_profit:.4f}"
            )
        
        return yes_order, no_order

    def settle_position(self, market_id: str, winning_outcome: OutcomeType) -> Decimal:
        """
        Simulate settlement of a market.
        
        Winning outcome pays $1 per share, losing pays $0.
        """
        pnl = Decimal("0")
        
        for outcome in [OutcomeType.YES, OutcomeType.NO]:
            pos_key = f"{market_id}_{outcome.value}"
            if pos_key in self.state.positions:
                pos = self.state.positions[pos_key]
                
                if outcome == winning_outcome:
                    # Winner: receive $1 per share
                    payout = pos.size * Decimal("1")
                    profit = payout - (pos.size * pos.avg_price)
                    self.state.current_balance += payout
                    pnl += profit
                    logger.info(f"✅ SETTLEMENT WIN | {pos.market_question} {outcome.value} | Payout: ${payout:.2f}")
                else:
                    # Loser: shares worth $0
                    loss = pos.size * pos.avg_price
                    pnl -= loss
                    logger.info(f"❌ SETTLEMENT LOSS | {pos.market_question} {outcome.value} | Loss: ${loss:.2f}")
                
                del self.state.positions[pos_key]
        
        return pnl

    def settle_arb_pair(self, market_id: str, winning_outcome: OutcomeType) -> Decimal:
        """
        Settle an arbitrage pair.
        
        For arbs, we always profit because we hold both sides.
        """
        yes_key = f"{market_id}_YES"
        no_key = f"{market_id}_NO"
        
        if yes_key not in self.state.positions or no_key not in self.state.positions:
            logger.warning(f"Incomplete arb pair for {market_id}")
            return self.settle_position(market_id, winning_outcome)
        
        yes_pos = self.state.positions[yes_key]
        no_pos = self.state.positions[no_key]
        
        # Calculate what we paid
        yes_cost = yes_pos.size * yes_pos.avg_price
        no_cost = no_pos.size * no_pos.avg_price
        total_cost = yes_cost + no_cost
        
        # We receive $1 per share on winning side
        size = min(yes_pos.size, no_pos.size)
        payout = size * Decimal("1")
        profit = payout - total_cost
        
        self.state.current_balance += payout
        
        del self.state.positions[yes_key]
        del self.state.positions[no_key]
        
        logger.info(
            f"🎯 ARB SETTLED | {yes_pos.market_question} | "
            f"Cost: ${total_cost:.4f} | Payout: ${payout:.2f} | PROFIT: ${profit:.4f}"
        )
        
        return profit

    def get_summary(self) -> dict:
        """Get simulation summary."""
        runtime = (datetime.utcnow() - self.state.started_at).total_seconds()
        
        return {
            "mode": "SIMULATION",
            "runtime_seconds": runtime,
            "initial_balance": float(self.state.initial_balance),
            "current_balance": float(self.state.current_balance),
            "portfolio_value": float(self.state.portfolio_value),
            "total_trades": self.state.total_trades,
            "open_positions": len(self.state.positions),
            "arb_pairs_executed": len(self.state.arb_pairs),
            "total_fees_paid": float(self.state.total_fees_paid),
            "realized_pnl": float(self.state.realized_pnl),
            "unrealized_pnl": float(self.state.unrealized_pnl),
            "total_pnl": float(self.state.total_pnl),
            "return_percent": float((self.state.total_pnl / self.state.initial_balance) * 100),
        }

    def get_trade_history(self) -> list[dict]:
        """Get trade history as list of dicts."""
        return [
            {
                "id": t.id,
                "timestamp": t.timestamp.isoformat(),
                "market": t.market_question,
                "outcome": t.outcome.value,
                "side": t.side.value,
                "price": float(t.price),
                "size": float(t.size),
                "fee": float(t.fee),
                "total_cost": float(t.total_cost),
            }
            for t in self.state.trades
        ]

    def get_positions(self) -> list[dict]:
        """Get current positions as list of dicts."""
        return [
            {
                "id": p.id,
                "market": p.market_question,
                "outcome": p.outcome.value,
                "size": float(p.size),
                "avg_price": float(p.avg_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pnl),
            }
            for p in self.state.positions.values()
        ]
