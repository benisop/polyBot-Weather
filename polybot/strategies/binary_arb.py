"""
Binary Arbitrage Strategy

Core strategy: Find YES/NO pairs where total cost < $1, buy both, lock profit at settlement.

How it works:
1. Continuously scan all binary markets
2. Detect when YES_price + NO_price < $0.98 (leaving room for fees)
3. Execute simultaneous buys on both outcomes
4. Hold until settlement - guaranteed profit regardless of outcome
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional

from loguru import logger

from polybot.connectors.polymarket import PolymarketConnector
from polybot.models import ArbOpportunity, Order, OutcomeType, PortfolioSnapshot


class BinaryArbStrategy:
    """Binary arbitrage strategy engine."""

    def __init__(
        self,
        connector: PolymarketConnector,
        min_profit_percent: Decimal = Decimal("1.5"),
        max_position_percent: Decimal = Decimal("5.0"),
        max_slippage_percent: Decimal = Decimal("2.0"),
        scan_interval_sec: float = 1.0,
    ):
        self.connector = connector
        self.min_profit_percent = min_profit_percent
        self.max_position_percent = max_position_percent
        self.max_slippage_percent = max_slippage_percent
        self.scan_interval_sec = scan_interval_sec

        # State
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._capital: Decimal = Decimal("0")
        self._deployed_capital: Decimal = Decimal("0")

        # Tracking
        self._opportunities_found: list[ArbOpportunity] = []
        self._executed_arbs: list[tuple[ArbOpportunity, Order, Order]] = []
        self._total_profit: Decimal = Decimal("0")
        self._trades_executed: int = 0
        self._start_time: Optional[datetime] = None

        # Rate limiting - don't spam same market
        self._recently_executed: dict[str, datetime] = {}
        self._cooldown_seconds = 60

    async def start(self, initial_capital: Decimal) -> None:
        """Start the strategy."""
        self._capital = initial_capital
        self._running = True
        self._start_time = datetime.utcnow()

        logger.info(
            f"Starting Binary Arb Strategy | Capital: ${initial_capital} | "
            f"Min Profit: {self.min_profit_percent}%"
        )

        # Fetch initial markets
        await self.connector.fetch_markets()

        # Start scanning loop
        self._scan_task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        """Stop the strategy."""
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        logger.info(
            f"Strategy stopped | Trades: {self._trades_executed} | "
            f"Total Profit: ${self._total_profit}"
        )

    async def _scan_loop(self) -> None:
        """Continuous market scanning loop."""
        while self._running:
            try:
                await self._scan_and_execute()
            except Exception as e:
                logger.error(f"Scan loop error: {e}")

            await asyncio.sleep(self.scan_interval_sec)

    async def _scan_and_execute(self) -> None:
        """Single scan iteration."""
        # Find opportunities
        opportunities = await self.connector.scan_arbitrage(self.min_profit_percent)
        self._opportunities_found = opportunities

        if not opportunities:
            return

        # Filter out recently executed markets
        now = datetime.utcnow()
        valid_opps = []
        for opp in opportunities:
            last_exec = self._recently_executed.get(opp.market.id)
            if last_exec is None or (now - last_exec).seconds > self._cooldown_seconds:
                valid_opps.append(opp)

        if not valid_opps:
            return

        # Execute best opportunity
        best = valid_opps[0]
        await self._execute_opportunity(best)

    async def _execute_opportunity(self, opp: ArbOpportunity) -> bool:
        """Execute a single arbitrage opportunity."""
        # Calculate position size
        available = self._capital - self._deployed_capital
        max_by_percent = self._capital * (self.max_position_percent / 100)
        max_by_available = available
        max_by_depth = opp.max_size

        size = min(max_by_percent, max_by_available, max_by_depth)

        if size < Decimal("1"):  # Minimum $1 position
            logger.debug(f"Size too small: ${size}")
            return False

        # Pre-execution slippage check
        current_cost = opp.yes_buy_price + opp.no_buy_price
        if current_cost >= Decimal("1"):
            logger.warning(f"Opportunity vanished: cost now ${current_cost}")
            return False

        # Execute
        logger.info(
            f"EXECUTING ARB: {opp.market.question[:50]}... | "
            f"Size: ${size} | Expected: {opp.net_profit_percent:.2f}%"
        )

        yes_order, no_order = await self.connector.execute_arbitrage(opp, size)

        if yes_order and no_order:
            self._executed_arbs.append((opp, yes_order, no_order))
            self._trades_executed += 1
            self._deployed_capital += size * opp.total_cost
            self._recently_executed[opp.market.id] = datetime.utcnow()

            # Log expected profit
            expected_profit = size * opp.net_profit
            logger.info(f"ARB EXECUTED | Expected profit: ${expected_profit:.4f}")

            return True

        return False

    def get_snapshot(self) -> PortfolioSnapshot:
        """Get current portfolio snapshot."""
        return PortfolioSnapshot(
            total_capital=self._capital,
            available_capital=self._capital - self._deployed_capital,
            deployed_capital=self._deployed_capital,
            unrealized_pnl=Decimal("0"),  # Would need position tracking
            realized_pnl=self._total_profit,
            open_positions=len(self._executed_arbs),
            total_trades=self._trades_executed,
        )

    def get_opportunities(self) -> list[ArbOpportunity]:
        """Get current arbitrage opportunities."""
        return self._opportunities_found

    def get_executed_arbs(self) -> list[tuple[ArbOpportunity, Order, Order]]:
        """Get list of executed arbitrages."""
        return self._executed_arbs

    def get_stats(self) -> dict:
        """Get strategy statistics."""
        runtime = (
            (datetime.utcnow() - self._start_time).total_seconds()
            if self._start_time
            else 0
        )

        return {
            "running": self._running,
            "runtime_seconds": runtime,
            "capital": float(self._capital),
            "deployed": float(self._deployed_capital),
            "available": float(self._capital - self._deployed_capital),
            "trades_executed": self._trades_executed,
            "total_profit": float(self._total_profit),
            "opportunities_current": len(self._opportunities_found),
            "min_profit_threshold": float(self.min_profit_percent),
        }
