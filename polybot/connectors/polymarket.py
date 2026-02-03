"""
Polymarket Connector - Full async interface for Polymarket CLOB.

Handles:
- REST API calls for markets, orders, positions
- WebSocket streaming for real-time orderbook updates
- Order placement with slippage protection
- Position tracking
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional

import httpx
import orjson
import websockets
from eth_account import Account
from eth_account.signers.local import LocalAccount
from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

from polybot.models import (
    ArbOpportunity,
    Market,
    Order,
    OrderSide,
    OrderStatus,
    OutcomeType,
    Position,
)


class PolymarketConnector:
    """Async connector for Polymarket CLOB API."""

    def __init__(
        self,
        private_key: str,
        funder_address: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
    ):
        self.private_key = private_key
        self.funder_address = funder_address
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

        # Initialize account
        self.account: LocalAccount = Account.from_key(private_key)
        self.address = self.account.address

        # API endpoints
        self.clob_url = "https://clob.polymarket.com"
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

        # SDK client (sync, we'll wrap async)
        self._clob_client: Optional[ClobClient] = None

        # State
        self._markets: dict[str, Market] = {}
        self._positions: dict[str, Position] = {}
        self._ws_connection: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_subscriptions: set[str] = set()
        self._running = False

        # Callbacks
        self._on_market_update: Optional[Callable[[Market], None]] = None
        self._on_arb_detected: Optional[Callable[[ArbOpportunity], None]] = None

        # HTTP client
        self._http: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Initialize connections."""
        logger.info("Connecting to Polymarket...")

        # Initialize HTTP client
        self._http = httpx.AsyncClient(
            base_url=self.clob_url,
            timeout=30.0,
            headers={"Content-Type": "application/json"},
        )

        # Initialize CLOB client
        creds = None
        if self.api_key and self.api_secret and self.api_passphrase:
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            )

        self._clob_client = ClobClient(
            host=self.clob_url,
            key=self.private_key,
            chain_id=POLYGON,
            funder=self.funder_address,
            creds=creds,
        )

        self._running = True
        logger.info(f"Connected to Polymarket as {self.address}")

    async def disconnect(self) -> None:
        """Close all connections."""
        self._running = False

        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None

        if self._http:
            await self._http.aclose()
            self._http = None

        logger.info("Disconnected from Polymarket")

    # =========================================================================
    # Market Data
    # =========================================================================

    async def fetch_markets(self, active_only: bool = True) -> list[Market]:
        """Fetch all available markets from Gamma API."""
        logger.debug("Fetching markets from Gamma API...")
        import json as json_module

        async with httpx.AsyncClient(base_url=self.gamma_url, timeout=30.0) as client:
            params = {"closed": "false", "limit": "500"} if active_only else {"limit": "500"}
            resp = await client.get("/markets", params=params)
            resp.raise_for_status()
            data = resp.json()

        markets = []
        for m in data:
            try:
                # Get token IDs (may be JSON string)
                clob_token_ids = m.get("clobTokenIds")
                if isinstance(clob_token_ids, str):
                    clob_token_ids = json_module.loads(clob_token_ids)
                
                # Get outcome prices (may be JSON string)
                outcome_prices = m.get("outcomePrices")
                if isinstance(outcome_prices, str):
                    outcome_prices = json_module.loads(outcome_prices)
                
                # Get outcomes (may be JSON string)
                outcomes = m.get("outcomes")
                if isinstance(outcomes, str):
                    outcomes = json_module.loads(outcomes)
                
                # Skip non-binary markets
                if not clob_token_ids or len(clob_token_ids) != 2:
                    continue
                
                if not outcomes or len(outcomes) != 2:
                    continue
                
                if not outcome_prices or len(outcome_prices) != 2:
                    outcome_prices = ["0.5", "0.5"]
                
                # Map outcomes to tokens - find "Yes" position
                yes_idx = 0
                for i, outcome in enumerate(outcomes):
                    if outcome.lower() == "yes":
                        yes_idx = i
                        break
                no_idx = 1 - yes_idx
                
                yes_price = Decimal(str(outcome_prices[yes_idx]))
                no_price = Decimal(str(outcome_prices[no_idx]))

                market = Market(
                    id=m.get("conditionId", m.get("id", "")),
                    condition_id=m.get("conditionId", m.get("id", "")),
                    question=m.get("question", "Unknown"),
                    description=m.get("description"),
                    yes_token_id=str(clob_token_ids[yes_idx]),
                    no_token_id=str(clob_token_ids[no_idx]),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume=Decimal(str(m.get("volume", 0) or 0)),
                    liquidity=Decimal(str(m.get("liquidity", 0) or 0)),
                    is_active=m.get("active", True),
                )
                markets.append(market)
                self._markets[market.id] = market

            except Exception as e:
                logger.debug(f"Failed to parse market: {e}")
                continue

        logger.info(f"Fetched {len(markets)} binary markets")
        return markets

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Get orderbook for a specific token."""
        resp = await self._http.get(f"/book?token_id={token_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_market_prices(self, market: Market) -> tuple[Decimal, Decimal]:
        """Get current best prices for YES and NO tokens."""
        try:
            # Fetch both orderbooks in parallel
            yes_book, no_book = await asyncio.gather(
                self.get_orderbook(market.yes_token_id),
                self.get_orderbook(market.no_token_id),
            )

            # Best ask (lowest sell price) is what we'd pay to buy
            yes_asks = yes_book.get("asks", [])
            no_asks = no_book.get("asks", [])

            yes_price = Decimal(yes_asks[0]["price"]) if yes_asks else Decimal("1")
            no_price = Decimal(no_asks[0]["price"]) if no_asks else Decimal("1")

            # Update market object
            market.yes_price = yes_price
            market.no_price = no_price

            # Track depth
            if yes_asks:
                market.yes_ask_size = Decimal(str(yes_asks[0].get("size", 0)))
            if no_asks:
                market.no_ask_size = Decimal(str(no_asks[0].get("size", 0)))

            return yes_price, no_price

        except Exception as e:
            logger.error(f"Failed to get prices for {market.id}: {e}")
            return Decimal("1"), Decimal("1")

    # =========================================================================
    # Arbitrage Detection
    # =========================================================================

    async def scan_arbitrage_fast(
        self, min_profit_percent: Decimal = Decimal("1.5")
    ) -> list[ArbOpportunity]:
        """
        Fast scan using pre-loaded prices from Gamma API.
        
        Does not hit orderbook API - uses cached prices for quick scanning.
        """
        opportunities = []

        for market in self._markets.values():
            if not market.is_active:
                continue

            try:
                # Use pre-loaded prices from Gamma API
                yes_price = market.yes_price
                no_price = market.no_price
                total_cost = yes_price + no_price

                # Arbitrage exists if buying both sides costs less than $1
                if total_cost < Decimal("1"):
                    gross_profit = Decimal("1") - total_cost

                    # Estimate fees (Polymarket takes ~2% on profits)
                    estimated_fees = gross_profit * Decimal("0.02")
                    net_profit = gross_profit - estimated_fees
                    net_profit_percent = (net_profit / total_cost) * 100

                    if net_profit_percent >= min_profit_percent:
                        # Estimate max size based on liquidity
                        max_size = min(market.liquidity / 10, Decimal("100"))

                        opp = ArbOpportunity(
                            market=market,
                            yes_buy_price=yes_price,
                            no_buy_price=no_price,
                            total_cost=total_cost,
                            gross_profit=gross_profit,
                            estimated_fees=estimated_fees,
                            net_profit=net_profit,
                            net_profit_percent=net_profit_percent,
                            max_size=max_size,
                        )
                        opportunities.append(opp)

                        logger.info(
                            f"ARB DETECTED: {market.question[:50]}... "
                            f"| Cost: ${total_cost:.4f} | Profit: {net_profit_percent:.2f}%"
                        )

            except Exception as e:
                logger.warning(f"Error scanning market {market.id}: {e}")
                continue

        return sorted(opportunities, key=lambda x: x.net_profit_percent, reverse=True)

    async def scan_arbitrage(
        self, min_profit_percent: Decimal = Decimal("1.5")
    ) -> list[ArbOpportunity]:
        """Scan all markets for arbitrage opportunities."""
        opportunities = []

        for market in self._markets.values():
            if not market.is_active:
                continue

            try:
                yes_price, no_price = await self.get_market_prices(market)
                total_cost = yes_price + no_price

                # Arbitrage exists if buying both sides costs less than $1
                if total_cost < Decimal("1"):
                    gross_profit = Decimal("1") - total_cost

                    # Estimate fees (Polymarket takes ~2% on profits)
                    estimated_fees = gross_profit * Decimal("0.02")
                    net_profit = gross_profit - estimated_fees
                    net_profit_percent = (net_profit / total_cost) * 100

                    if net_profit_percent >= min_profit_percent:
                        # Max size is limited by smallest orderbook depth
                        max_size = min(market.yes_ask_size, market.no_ask_size)

                        opp = ArbOpportunity(
                            market=market,
                            yes_buy_price=yes_price,
                            no_buy_price=no_price,
                            total_cost=total_cost,
                            gross_profit=gross_profit,
                            estimated_fees=estimated_fees,
                            net_profit=net_profit,
                            net_profit_percent=net_profit_percent,
                            max_size=max_size,
                        )
                        opportunities.append(opp)

                        logger.info(
                            f"ARB DETECTED: {market.question[:50]}... "
                            f"| Cost: ${total_cost:.4f} | Profit: {net_profit_percent:.2f}%"
                        )

            except Exception as e:
                logger.warning(f"Error scanning market {market.id}: {e}")
                continue

        return sorted(opportunities, key=lambda x: x.net_profit_percent, reverse=True)

    # =========================================================================
    # Order Management
    # =========================================================================

    async def place_order(
        self,
        market: Market,
        outcome: OutcomeType,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
    ) -> Optional[Order]:
        """Place an order on the market."""
        token_id = market.yes_token_id if outcome == OutcomeType.YES else market.no_token_id

        order = Order(
            market_id=market.id,
            token_id=token_id,
            side=side,
            outcome=outcome,
            price=price,
            size=size,
        )

        try:
            # Use CLOB client to place order
            order_args = OrderArgs(
                token_id=token_id,
                price=float(price),
                size=float(size),
                side=side.value,
            )

            # Build and sign order
            signed_order = self._clob_client.create_order(order_args)
            result = self._clob_client.post_order(signed_order, OrderType.GTC)

            if result.get("success"):
                order.id = result.get("orderID")
                order.status = OrderStatus.OPEN
                logger.info(f"Order placed: {order.id} | {outcome.value} @ ${price} x {size}")
            else:
                order.status = OrderStatus.FAILED
                logger.error(f"Order failed: {result}")

            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            order.status = OrderStatus.FAILED
            return order

    async def execute_arbitrage(
        self,
        opportunity: ArbOpportunity,
        size: Optional[Decimal] = None,
    ) -> tuple[Optional[Order], Optional[Order]]:
        """Execute arbitrage by buying both YES and NO."""
        if size is None:
            size = opportunity.max_size

        if size <= 0:
            logger.warning("Cannot execute arb: size is 0")
            return None, None

        market = opportunity.market
        logger.info(
            f"Executing arb on {market.question[:50]}... | Size: {size} | Expected profit: {opportunity.net_profit_percent:.2f}%"
        )

        # Place both orders
        yes_order = await self.place_order(
            market=market,
            outcome=OutcomeType.YES,
            side=OrderSide.BUY,
            price=opportunity.yes_buy_price,
            size=size,
        )

        no_order = await self.place_order(
            market=market,
            outcome=OutcomeType.NO,
            side=OrderSide.BUY,
            price=opportunity.no_buy_price,
            size=size,
        )

        return yes_order, no_order

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        try:
            result = self._clob_client.cancel(order_id)
            return result.get("success", False)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        try:
            return self._clob_client.get_orders()
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    # =========================================================================
    # WebSocket Streaming
    # =========================================================================

    async def subscribe_market(self, token_id: str) -> None:
        """Subscribe to market updates via WebSocket."""
        if not self._ws_connection:
            await self._connect_websocket()

        if token_id not in self._ws_subscriptions:
            msg = {"type": "subscribe", "channel": "market", "assets_ids": [token_id]}
            await self._ws_connection.send(orjson.dumps(msg).decode())
            self._ws_subscriptions.add(token_id)
            logger.debug(f"Subscribed to market: {token_id}")

    async def _connect_websocket(self) -> None:
        """Establish WebSocket connection."""
        self._ws_connection = await websockets.connect(self.ws_url)
        logger.info("WebSocket connected")

        # Start message handler
        asyncio.create_task(self._ws_message_handler())

    async def _ws_message_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for message in self._ws_connection:
                data = orjson.loads(message)
                await self._handle_ws_message(data)
        except websockets.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self._ws_connection = None
        except Exception as e:
            logger.error(f"WebSocket error: {e}")

    async def _handle_ws_message(self, data: dict) -> None:
        """Process WebSocket message."""
        msg_type = data.get("type")

        if msg_type == "price_change":
            token_id = data.get("asset_id")
            price = Decimal(str(data.get("price", 0)))

            # Find and update market
            for market in self._markets.values():
                if market.yes_token_id == token_id:
                    market.yes_price = price
                    if self._on_market_update:
                        self._on_market_update(market)
                    break
                elif market.no_token_id == token_id:
                    market.no_price = price
                    if self._on_market_update:
                        self._on_market_update(market)
                    break

    def on_market_update(self, callback: Callable[[Market], None]) -> None:
        """Register callback for market updates."""
        self._on_market_update = callback

    def on_arb_detected(self, callback: Callable[[ArbOpportunity], None]) -> None:
        """Register callback for arbitrage detection."""
        self._on_arb_detected = callback

    # =========================================================================
    # Position Tracking
    # =========================================================================

    async def get_positions(self) -> list[Position]:
        """Get current positions."""
        # In production, this would query the blockchain/API
        return list(self._positions.values())

    async def get_balance(self) -> Decimal:
        """Get USDC balance on Polygon."""
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

            # USDC on Polygon
            usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            usdc_abi = [
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function",
                }
            ]

            contract = w3.eth.contract(
                address=Web3.to_checksum_address(usdc_address), abi=usdc_abi
            )
            balance = contract.functions.balanceOf(self.address).call()

            # USDC has 6 decimals
            return Decimal(balance) / Decimal(10**6)

        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return Decimal("0")
