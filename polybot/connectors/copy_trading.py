"""
Copy Trading Connector

Monitors top Polymarket trader wallets on-chain and mirrors their trades.
Uses delayed execution (30-60s) for latency-tolerant setups.

Strategy rationale:
- Top traders have information/analytical edge
- Copying with delay still captures most alpha on longer-duration markets
- No need for sub-second execution - works well on Mac Mini from Alabama
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Callable
from dataclasses import dataclass, field

import httpx
from loguru import logger
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from polybot.models import Market, Order, OrderSide, OutcomeType


# Polymarket CTF (Conditional Token Framework) contract on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# ERC1155 Transfer event signature
TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"


@dataclass
class TopTrader:
    """A tracked top trader."""
    
    address: str
    name: str  # Optional human-readable name
    win_rate: float  # Historical win rate (0-1)
    total_profit: Decimal  # Total historical profit in USD
    avg_position_size: Decimal  # Average position size
    specialty: str  # e.g., "politics", "crypto", "sports", "weather"
    
    # Tracking
    last_trade_time: Optional[datetime] = None
    trades_copied: int = 0
    copy_pnl: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class DetectedTrade:
    """A trade detected from a top trader."""
    
    trader: TopTrader
    market_id: str
    token_id: str
    outcome: OutcomeType
    side: OrderSide
    size: Decimal
    price: Decimal  # Estimated from trade value
    timestamp: datetime
    tx_hash: str
    
    # Execution tracking
    copied: bool = False
    copy_order_id: Optional[str] = None
    copy_delay_seconds: float = 0
    
    def __repr__(self) -> str:
        return (
            f"DetectedTrade({self.trader.name} | "
            f"{self.side.value} {self.outcome.value} | "
            f"${self.size} @ ${self.price} | "
            f"{self.timestamp.strftime('%H:%M:%S')})"
        )


# Curated list of known successful Polymarket traders
# These are example addresses - you'd want to research actual top performers
DEFAULT_TOP_TRADERS = [
    TopTrader(
        address="0x1234567890abcdef1234567890abcdef12345678",  # Placeholder
        name="Trader_Alpha",
        win_rate=0.72,
        total_profit=Decimal("150000"),
        avg_position_size=Decimal("5000"),
        specialty="politics",
    ),
    TopTrader(
        address="0xabcdef1234567890abcdef1234567890abcdef12",  # Placeholder
        name="WeatherWhale",
        win_rate=0.68,
        total_profit=Decimal("85000"),
        avg_position_size=Decimal("2500"),
        specialty="weather",
    ),
    TopTrader(
        address="0x9876543210fedcba9876543210fedcba98765432",  # Placeholder  
        name="CryptoOracle",
        win_rate=0.65,
        total_profit=Decimal("220000"),
        avg_position_size=Decimal("10000"),
        specialty="crypto",
    ),
]


class CopyTradingConnector:
    """
    Monitors top trader wallets and copies their Polymarket trades.
    
    Design for latency tolerance:
    - Polls every 10-15 seconds (not real-time websocket)
    - Executes copies with 30-60 second delay
    - Targets markets with >24h duration where speed doesn't matter
    """

    def __init__(
        self,
        rpc_url: str = "https://polygon-rpc.com",
        copy_delay_seconds: float = 45.0,  # Wait before copying
        max_copy_size: Decimal = Decimal("100"),  # Max per copy trade
        copy_fraction: float = 0.10,  # Copy 10% of their size
        min_trader_win_rate: float = 0.60,  # Only copy traders with >60% win rate
    ):
        self.rpc_url = rpc_url
        self.copy_delay = copy_delay_seconds
        self.max_copy_size = max_copy_size
        self.copy_fraction = copy_fraction
        self.min_win_rate = min_trader_win_rate
        
        self._traders: dict[str, TopTrader] = {}
        self._pending_copies: list[DetectedTrade] = []
        self._executed_copies: list[DetectedTrade] = []
        self._running = False
        
        # Web3 connection
        self._w3: Optional[AsyncWeb3] = None
        self._http: Optional[httpx.AsyncClient] = None
        
        # Callbacks
        self._on_trade_detected: Optional[Callable[[DetectedTrade], None]] = None
        self._on_copy_executed: Optional[Callable[[DetectedTrade, Order], None]] = None

    async def connect(self) -> None:
        """Initialize connections."""
        logger.info("Connecting to Polygon for copy trading...")
        
        self._w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
        self._http = httpx.AsyncClient(timeout=30.0)
        
        # Verify connection
        chain_id = await self._w3.eth.chain_id
        if chain_id != 137:
            logger.warning(f"Expected Polygon (137), got chain {chain_id}")
        
        self._running = True
        logger.info(f"Connected to Polygon (chain {chain_id})")

    async def disconnect(self) -> None:
        """Close connections."""
        self._running = False
        
        if self._http:
            await self._http.aclose()
            self._http = None
        
        logger.info("Disconnected copy trading connector")

    def add_trader(self, trader: TopTrader) -> None:
        """Add a trader to track."""
        if trader.win_rate < self.min_win_rate:
            logger.warning(f"Trader {trader.name} win rate {trader.win_rate:.0%} below minimum {self.min_win_rate:.0%}")
            return
        
        self._traders[trader.address.lower()] = trader
        logger.info(f"Tracking trader: {trader.name} ({trader.address[:10]}...) - {trader.specialty}")

    def add_default_traders(self) -> None:
        """Add the default curated trader list."""
        for trader in DEFAULT_TOP_TRADERS:
            self.add_trader(trader)
        logger.info(f"Added {len(self._traders)} default traders")

    async def fetch_trader_history(self, address: str) -> list[dict]:
        """
        Fetch recent trade history for a trader from Polygonscan/Dune.
        
        This is a simplified version - production would use:
        - Dune Analytics API for historical analysis
        - Polygonscan API for recent transactions
        - Custom indexer for real-time
        """
        try:
            # Query Polygonscan for recent ERC1155 transfers
            # This is a simplified example - you'd need an API key
            url = f"https://api.polygonscan.com/api"
            params = {
                "module": "account",
                "action": "token1155tx",
                "address": address,
                "page": 1,
                "offset": 50,
                "sort": "desc",
            }
            
            resp = await self._http.get(url, params=params)
            data = resp.json()
            
            if data.get("status") == "1":
                return data.get("result", [])
            
            return []
            
        except Exception as e:
            logger.warning(f"Failed to fetch history for {address}: {e}")
            return []

    async def detect_new_trades(self) -> list[DetectedTrade]:
        """
        Scan for new trades from tracked wallets.
        
        Uses recent block logs to find CTF token transfers.
        """
        if not self._w3:
            return []
        
        detected = []
        
        try:
            # Get recent blocks (last ~30 seconds worth)
            latest_block = await self._w3.eth.block_number
            from_block = latest_block - 15  # ~30 seconds on Polygon
            
            # Build filter for ERC1155 transfers from tracked addresses
            tracked_addresses = list(self._traders.keys())
            
            if not tracked_addresses:
                return []
            
            # Query transfer events
            # Note: This is simplified - production would batch and optimize
            filter_params = {
                "fromBlock": from_block,
                "toBlock": "latest",
                "address": CTF_ADDRESS,
                "topics": [TRANSFER_SINGLE_TOPIC],
            }
            
            logs = await self._w3.eth.get_logs(filter_params)
            
            for log in logs:
                try:
                    # Decode log data
                    tx_hash = log["transactionHash"].hex()
                    
                    # Extract from address (topic[2])
                    from_addr = "0x" + log["topics"][2].hex()[-40:]
                    to_addr = "0x" + log["topics"][3].hex()[-40:]
                    
                    # Check if from or to is a tracked trader
                    trader = None
                    is_buy = False
                    
                    if to_addr.lower() in self._traders:
                        trader = self._traders[to_addr.lower()]
                        is_buy = True
                    elif from_addr.lower() in self._traders:
                        trader = self._traders[from_addr.lower()]
                        is_buy = False
                    
                    if not trader:
                        continue
                    
                    # Decode token ID and amount from data
                    data = log["data"].hex()
                    token_id = int(data[2:66], 16)
                    amount = int(data[66:130], 16)
                    
                    # Create detected trade
                    trade = DetectedTrade(
                        trader=trader,
                        market_id=str(token_id)[:20],  # Truncated for logging
                        token_id=str(token_id),
                        outcome=OutcomeType.YES,  # Would need token mapping to determine
                        side=OrderSide.BUY if is_buy else OrderSide.SELL,
                        size=Decimal(str(amount)) / Decimal("1e6"),  # Assuming 6 decimals
                        price=Decimal("0.50"),  # Would need to look up actual price
                        timestamp=datetime.utcnow(),
                        tx_hash=tx_hash,
                    )
                    
                    detected.append(trade)
                    logger.info(f"🔍 DETECTED: {trade}")
                    
                    if self._on_trade_detected:
                        self._on_trade_detected(trade)
                    
                except Exception as e:
                    logger.debug(f"Failed to parse log: {e}")
                    continue
            
        except Exception as e:
            logger.warning(f"Failed to detect trades: {e}")
        
        return detected

    async def queue_copy(self, trade: DetectedTrade) -> None:
        """Queue a trade for delayed copying."""
        
        # Calculate copy size
        copy_size = trade.size * Decimal(str(self.copy_fraction))
        copy_size = min(copy_size, self.max_copy_size)
        copy_size = max(copy_size, Decimal("1"))  # Minimum $1
        
        # Create modified trade for copying
        copy_trade = DetectedTrade(
            trader=trade.trader,
            market_id=trade.market_id,
            token_id=trade.token_id,
            outcome=trade.outcome,
            side=trade.side,
            size=copy_size,
            price=trade.price,
            timestamp=trade.timestamp,
            tx_hash=trade.tx_hash,
            copy_delay_seconds=self.copy_delay,
        )
        
        self._pending_copies.append(copy_trade)
        logger.info(f"📋 QUEUED COPY: {copy_trade} (delay: {self.copy_delay}s)")

    async def execute_pending_copies(
        self,
        polymarket_connector,  # PolymarketConnector
    ) -> list[Order]:
        """Execute any pending copies that have passed their delay."""
        
        now = datetime.utcnow()
        executed_orders = []
        still_pending = []
        
        for trade in self._pending_copies:
            delay_passed = (now - trade.timestamp).total_seconds() >= trade.copy_delay_seconds
            
            if delay_passed:
                try:
                    # Execute the copy trade
                    # Note: Would need to look up market details from token_id
                    logger.info(f"🚀 EXECUTING COPY: {trade}")
                    
                    # In production, this would:
                    # 1. Look up market from token_id
                    # 2. Get current price
                    # 3. Place order via polymarket_connector
                    
                    trade.copied = True
                    self._executed_copies.append(trade)
                    
                    # Update trader stats
                    trade.trader.last_trade_time = now
                    trade.trader.trades_copied += 1
                    
                except Exception as e:
                    logger.error(f"Failed to execute copy: {e}")
                    still_pending.append(trade)
            else:
                still_pending.append(trade)
        
        self._pending_copies = still_pending
        return executed_orders

    async def run_monitor_loop(
        self,
        polymarket_connector=None,
        poll_interval: float = 15.0,
    ) -> None:
        """
        Main monitoring loop.
        
        Polls for new trades and executes delayed copies.
        """
        logger.info(f"Starting copy trading monitor (poll every {poll_interval}s)...")
        
        while self._running:
            try:
                # Detect new trades
                new_trades = await self.detect_new_trades()
                
                # Queue copies for detected trades
                for trade in new_trades:
                    if trade.side == OrderSide.BUY:  # Only copy buys
                        await self.queue_copy(trade)
                
                # Execute pending copies
                if polymarket_connector:
                    await self.execute_pending_copies(polymarket_connector)
                
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            
            await asyncio.sleep(poll_interval)

    def get_pending_copies(self) -> list[DetectedTrade]:
        """Get pending copy trades."""
        return self._pending_copies

    def get_executed_copies(self) -> list[DetectedTrade]:
        """Get executed copy trades."""
        return self._executed_copies

    def get_trader_stats(self) -> dict:
        """Get statistics for all tracked traders."""
        return {
            "tracked_traders": len(self._traders),
            "pending_copies": len(self._pending_copies),
            "executed_copies": len(self._executed_copies),
            "traders": [
                {
                    "name": t.name,
                    "address": t.address[:10] + "...",
                    "win_rate": t.win_rate,
                    "specialty": t.specialty,
                    "trades_copied": t.trades_copied,
                    "copy_pnl": float(t.copy_pnl),
                }
                for t in self._traders.values()
            ],
        }


async def discover_top_traders(
    min_profit: Decimal = Decimal("50000"),
    min_trades: int = 100,
    min_win_rate: float = 0.60,
) -> list[TopTrader]:
    """
    Discover top traders from on-chain data.
    
    In production, this would:
    1. Query Dune Analytics for historical Polymarket trades
    2. Calculate win rates and P&L per wallet
    3. Filter by criteria and return top performers
    
    For now, returns placeholder data.
    """
    logger.info("Discovering top traders (this would query Dune/subgraph in production)...")
    
    # Placeholder - in production query Dune/The Graph
    return DEFAULT_TOP_TRADERS
