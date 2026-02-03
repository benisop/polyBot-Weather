"""
Historical Data Persistence Layer

Stores market snapshots, trades, and signals for backtesting.
Uses SQLite (async) for simplicity and portability.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, List
import json

import aiosqlite
from loguru import logger

from polybot.models import Market, Order, OrderSide, OutcomeType, ArbOpportunity


class DataStore:
    """
    Async SQLite-based data persistence for backtesting and analysis.
    
    Stores:
    - Market snapshots (prices over time)
    - Executed trades
    - Strategy signals
    - Performance metrics
    """

    def __init__(self, db_path: str = "data/polybot.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Connect to database and create tables."""
        logger.info(f"Connecting to database: {self.db_path}")
        
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        
        await self._create_tables()
        logger.info("Database connected and tables initialized")

    async def disconnect(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
        logger.info("Database disconnected")

    async def _create_tables(self) -> None:
        """Create database schema."""
        
        # Market snapshots - price history
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                question TEXT NOT NULL,
                yes_price REAL NOT NULL,
                no_price REAL NOT NULL,
                volume REAL DEFAULT 0,
                liquidity REAL DEFAULT 0,
                yes_bid_size REAL DEFAULT 0,
                yes_ask_size REAL DEFAULT 0,
                no_bid_size REAL DEFAULT 0,
                no_ask_size REAL DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                -- Index for efficient time-series queries
                UNIQUE(market_id, timestamp)
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_market_time 
            ON market_snapshots(market_id, timestamp DESC)
        """)
        
        # Executed trades
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                market_id TEXT NOT NULL,
                market_question TEXT,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                filled_size REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                strategy TEXT,  -- e.g., 'weather_v2', 'binary_arb', 'copy'
                signal_id TEXT,  -- Reference to generating signal
                status TEXT DEFAULT 'PENDING',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                filled_at DATETIME,
                
                -- P&L tracking
                realized_pnl REAL,
                settlement_price REAL,
                settled_at DATETIME
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_strategy 
            ON trades(strategy, created_at DESC)
        """)
        
        # Strategy signals (before execution)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT UNIQUE NOT NULL,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                market_question TEXT,
                
                -- Signal details
                market_price REAL NOT NULL,
                forecast_price REAL NOT NULL,
                edge REAL NOT NULL,
                edge_zscore REAL,
                recommended_side TEXT NOT NULL,
                confidence TEXT,
                kelly_fraction REAL,
                reasoning TEXT,
                
                -- Execution tracking
                executed INTEGER DEFAULT 0,
                trade_id INTEGER,
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_strategy 
            ON signals(strategy, created_at DESC)
        """)
        
        # Arbitrage opportunities
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS arb_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                market_question TEXT,
                yes_price REAL NOT NULL,
                no_price REAL NOT NULL,
                total_cost REAL NOT NULL,
                gross_profit REAL NOT NULL,
                net_profit REAL NOT NULL,
                net_profit_percent REAL NOT NULL,
                max_size REAL,
                
                -- Execution
                executed INTEGER DEFAULT 0,
                execution_profit REAL,
                
                detected_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Daily performance summary
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE UNIQUE NOT NULL,
                
                -- Trade counts
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                
                -- P&L
                gross_pnl REAL DEFAULT 0,
                fees_paid REAL DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                
                -- By strategy
                weather_trades INTEGER DEFAULT 0,
                weather_pnl REAL DEFAULT 0,
                arb_trades INTEGER DEFAULT 0,
                arb_pnl REAL DEFAULT 0,
                copy_trades INTEGER DEFAULT 0,
                copy_pnl REAL DEFAULT 0,
                
                -- Risk metrics
                max_drawdown REAL DEFAULT 0,
                sharpe_ratio REAL,
                
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await self._db.commit()

    # =========================================================================
    # Market Snapshots
    # =========================================================================

    async def save_market_snapshot(self, market: Market) -> None:
        """Save a market price snapshot."""
        await self._db.execute("""
            INSERT OR REPLACE INTO market_snapshots 
            (market_id, condition_id, question, yes_price, no_price, 
             volume, liquidity, yes_bid_size, yes_ask_size, 
             no_bid_size, no_ask_size, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market.id,
            market.condition_id,
            market.question,
            float(market.yes_price),
            float(market.no_price),
            float(market.volume),
            float(market.liquidity),
            float(market.yes_bid_size),
            float(market.yes_ask_size),
            float(market.no_bid_size),
            float(market.no_ask_size),
            datetime.utcnow().isoformat(),
        ))
        await self._db.commit()

    async def save_market_snapshots(self, markets: List[Market]) -> None:
        """Batch save multiple market snapshots."""
        now = datetime.utcnow().isoformat()
        
        data = [
            (
                m.id, m.condition_id, m.question,
                float(m.yes_price), float(m.no_price),
                float(m.volume), float(m.liquidity),
                float(m.yes_bid_size), float(m.yes_ask_size),
                float(m.no_bid_size), float(m.no_ask_size),
                now,
            )
            for m in markets
        ]
        
        await self._db.executemany("""
            INSERT OR REPLACE INTO market_snapshots 
            (market_id, condition_id, question, yes_price, no_price, 
             volume, liquidity, yes_bid_size, yes_ask_size, 
             no_bid_size, no_ask_size, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
        await self._db.commit()
        
        logger.debug(f"Saved {len(markets)} market snapshots")

    async def get_market_history(
        self,
        market_id: str,
        hours: int = 24,
    ) -> List[dict]:
        """Get price history for a market."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        
        cursor = await self._db.execute("""
            SELECT * FROM market_snapshots
            WHERE market_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (market_id, cutoff))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_markets_latest(self) -> List[dict]:
        """Get latest snapshot for all markets."""
        cursor = await self._db.execute("""
            SELECT * FROM market_snapshots
            WHERE (market_id, timestamp) IN (
                SELECT market_id, MAX(timestamp)
                FROM market_snapshots
                GROUP BY market_id
            )
        """)
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # =========================================================================
    # Trades
    # =========================================================================

    async def save_trade(
        self,
        order: Order,
        strategy: str,
        market_question: str = "",
        signal_id: Optional[str] = None,
    ) -> int:
        """Save an executed trade."""
        cursor = await self._db.execute("""
            INSERT INTO trades 
            (order_id, market_id, market_question, token_id, outcome, side,
             price, size, filled_size, strategy, signal_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.id,
            order.market_id,
            market_question,
            order.token_id,
            order.outcome.value,
            order.side.value,
            float(order.price),
            float(order.size),
            float(order.filled_size),
            strategy,
            signal_id,
            order.status.value,
            datetime.utcnow().isoformat(),
        ))
        await self._db.commit()
        
        logger.info(f"Saved trade: {strategy} {order.side.value} {order.outcome.value} ${order.size}")
        return cursor.lastrowid

    async def update_trade_settlement(
        self,
        trade_id: int,
        settlement_price: Decimal,
        realized_pnl: Decimal,
    ) -> None:
        """Update a trade with settlement info."""
        await self._db.execute("""
            UPDATE trades
            SET settlement_price = ?, realized_pnl = ?, settled_at = ?
            WHERE id = ?
        """, (
            float(settlement_price),
            float(realized_pnl),
            datetime.utcnow().isoformat(),
            trade_id,
        ))
        await self._db.commit()

    async def get_trades(
        self,
        strategy: Optional[str] = None,
        days: int = 30,
    ) -> List[dict]:
        """Get trade history."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        if strategy:
            cursor = await self._db.execute("""
                SELECT * FROM trades
                WHERE strategy = ? AND created_at >= ?
                ORDER BY created_at DESC
            """, (strategy, cutoff))
        else:
            cursor = await self._db.execute("""
                SELECT * FROM trades
                WHERE created_at >= ?
                ORDER BY created_at DESC
            """, (cutoff,))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_trade_stats(self, days: int = 30) -> dict:
        """Get aggregate trade statistics."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        cursor = await self._db.execute("""
            SELECT 
                strategy,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losing,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl,
                SUM(size * price) as total_volume
            FROM trades
            WHERE created_at >= ?
            GROUP BY strategy
        """, (cutoff,))
        
        rows = await cursor.fetchall()
        return {row["strategy"]: dict(row) for row in rows}

    # =========================================================================
    # Signals
    # =========================================================================

    async def save_signal(
        self,
        signal_id: str,
        strategy: str,
        market_id: str,
        market_question: str,
        market_price: Decimal,
        forecast_price: Decimal,
        edge: Decimal,
        recommended_side: str,
        edge_zscore: Optional[float] = None,
        confidence: Optional[str] = None,
        kelly_fraction: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> None:
        """Save a strategy signal."""
        await self._db.execute("""
            INSERT OR REPLACE INTO signals 
            (signal_id, strategy, market_id, market_question, market_price,
             forecast_price, edge, edge_zscore, recommended_side, confidence,
             kelly_fraction, reasoning, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_id,
            strategy,
            market_id,
            market_question,
            float(market_price),
            float(forecast_price),
            float(edge),
            edge_zscore,
            recommended_side,
            confidence,
            kelly_fraction,
            reasoning,
            datetime.utcnow().isoformat(),
        ))
        await self._db.commit()

    async def mark_signal_executed(
        self,
        signal_id: str,
        trade_id: int,
    ) -> None:
        """Mark a signal as executed."""
        await self._db.execute("""
            UPDATE signals
            SET executed = 1, trade_id = ?
            WHERE signal_id = ?
        """, (trade_id, signal_id))
        await self._db.commit()

    async def get_signal_performance(
        self,
        strategy: str,
        days: int = 30,
    ) -> dict:
        """Get signal accuracy and performance."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        cursor = await self._db.execute("""
            SELECT 
                s.confidence,
                COUNT(*) as total_signals,
                SUM(s.executed) as executed_signals,
                AVG(s.edge) as avg_edge,
                AVG(s.edge_zscore) as avg_zscore,
                SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as winning,
                SUM(t.realized_pnl) as total_pnl
            FROM signals s
            LEFT JOIN trades t ON s.trade_id = t.id
            WHERE s.strategy = ? AND s.created_at >= ?
            GROUP BY s.confidence
        """, (strategy, cutoff))
        
        rows = await cursor.fetchall()
        return {row["confidence"]: dict(row) for row in rows}

    # =========================================================================
    # Arbitrage Opportunities
    # =========================================================================

    async def save_arb_opportunity(self, opp: ArbOpportunity) -> int:
        """Save an arbitrage opportunity."""
        cursor = await self._db.execute("""
            INSERT INTO arb_opportunities 
            (market_id, market_question, yes_price, no_price, total_cost,
             gross_profit, net_profit, net_profit_percent, max_size, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opp.market.id,
            opp.market.question,
            float(opp.yes_buy_price),
            float(opp.no_buy_price),
            float(opp.total_cost),
            float(opp.gross_profit),
            float(opp.net_profit),
            float(opp.net_profit_percent),
            float(opp.max_size),
            opp.detected_at.isoformat(),
        ))
        await self._db.commit()
        return cursor.lastrowid

    # =========================================================================
    # Daily Performance
    # =========================================================================

    async def update_daily_performance(self) -> None:
        """Update today's performance summary."""
        today = datetime.utcnow().date().isoformat()
        
        # Calculate stats from today's trades
        cursor = await self._db.execute("""
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losing,
                SUM(realized_pnl) as net_pnl,
                SUM(fees) as fees_paid,
                SUM(CASE WHEN strategy = 'weather_v2' THEN 1 ELSE 0 END) as weather_trades,
                SUM(CASE WHEN strategy = 'weather_v2' THEN realized_pnl ELSE 0 END) as weather_pnl,
                SUM(CASE WHEN strategy = 'binary_arb' THEN 1 ELSE 0 END) as arb_trades,
                SUM(CASE WHEN strategy = 'binary_arb' THEN realized_pnl ELSE 0 END) as arb_pnl,
                SUM(CASE WHEN strategy = 'copy' THEN 1 ELSE 0 END) as copy_trades,
                SUM(CASE WHEN strategy = 'copy' THEN realized_pnl ELSE 0 END) as copy_pnl
            FROM trades
            WHERE DATE(created_at) = ?
        """, (today,))
        
        row = await cursor.fetchone()
        
        if row:
            await self._db.execute("""
                INSERT OR REPLACE INTO daily_performance 
                (date, total_trades, winning_trades, losing_trades, net_pnl, fees_paid,
                 weather_trades, weather_pnl, arb_trades, arb_pnl, 
                 copy_trades, copy_pnl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                row["total_trades"] or 0,
                row["winning"] or 0,
                row["losing"] or 0,
                row["net_pnl"] or 0,
                row["fees_paid"] or 0,
                row["weather_trades"] or 0,
                row["weather_pnl"] or 0,
                row["arb_trades"] or 0,
                row["arb_pnl"] or 0,
                row["copy_trades"] or 0,
                row["copy_pnl"] or 0,
                datetime.utcnow().isoformat(),
            ))
            await self._db.commit()

    async def get_performance_history(self, days: int = 30) -> List[dict]:
        """Get daily performance history."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        
        cursor = await self._db.execute("""
            SELECT * FROM daily_performance
            WHERE date >= ?
            ORDER BY date ASC
        """, (cutoff,))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # =========================================================================
    # Backtesting Support
    # =========================================================================

    async def get_market_prices_at_time(
        self,
        market_id: str,
        timestamp: datetime,
    ) -> Optional[dict]:
        """Get market prices at a specific historical time (for backtesting)."""
        cursor = await self._db.execute("""
            SELECT * FROM market_snapshots
            WHERE market_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (market_id, timestamp.isoformat()))
        
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def simulate_signal_performance(
        self,
        strategy: str,
        days: int = 30,
    ) -> dict:
        """
        Simulate P&L if all signals had been executed.
        
        Useful for comparing actual vs theoretical performance.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        cursor = await self._db.execute("""
            SELECT 
                SUM(edge * 100) as theoretical_edge_sum,
                AVG(edge) as avg_edge,
                COUNT(*) as total_signals,
                SUM(executed) as executed_signals,
                SUM(CASE WHEN executed = 1 THEN edge ELSE 0 END) as captured_edge
            FROM signals
            WHERE strategy = ? AND created_at >= ?
        """, (strategy, cutoff))
        
        row = await cursor.fetchone()
        return dict(row) if row else {}


# Convenience function for creating a configured datastore
async def create_datastore(db_path: str = "data/polybot.db") -> DataStore:
    """Create and connect a datastore instance."""
    store = DataStore(db_path)
    await store.connect()
    return store
