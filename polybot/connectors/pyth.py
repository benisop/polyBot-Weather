"""
Pyth Network Connector - Real-time oracle price feeds.

Provides crypto price data for:
- Cross-referencing with market predictions
- Detecting mispriced markets
- Settlement verification
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from loguru import logger
from pydantic import BaseModel


class PriceData(BaseModel):
    """Price data from Pyth oracle."""

    feed_id: str
    symbol: str
    price: Decimal
    confidence: Decimal
    expo: int
    publish_time: datetime

    @property
    def price_adjusted(self) -> Decimal:
        """Get price adjusted for exponent."""
        return self.price * Decimal(10) ** self.expo


class PythConnector:
    """Async connector for Pyth Network Hermes API."""

    # Common price feed IDs
    FEEDS = {
        "XRP/USD": "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
        "BTC/USD": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
        "ETH/USD": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
        "SOL/USD": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
        "DOGE/USD": "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    }

    def __init__(self, hermes_url: str = "https://hermes.pyth.network"):
        self.hermes_url = hermes_url
        self._http: Optional[httpx.AsyncClient] = None
        self._prices: dict[str, PriceData] = {}
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Initialize connection."""
        self._http = httpx.AsyncClient(
            base_url=self.hermes_url,
            timeout=10.0,
        )
        self._running = True
        logger.info("Pyth connector initialized")

    async def disconnect(self) -> None:
        """Close connection."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        if self._http:
            await self._http.aclose()
        logger.info("Pyth connector disconnected")

    async def get_price(self, feed_id: str) -> Optional[PriceData]:
        """Get latest price for a feed."""
        try:
            resp = await self._http.get(
                "/v2/updates/price/latest",
                params={"ids[]": feed_id},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("parsed"):
                return None

            parsed = data["parsed"][0]
            price_info = parsed["price"]

            # Find symbol
            symbol = next(
                (k for k, v in self.FEEDS.items() if v == feed_id),
                "UNKNOWN",
            )

            price_data = PriceData(
                feed_id=feed_id,
                symbol=symbol,
                price=Decimal(price_info["price"]),
                confidence=Decimal(price_info["conf"]),
                expo=int(price_info["expo"]),
                publish_time=datetime.fromtimestamp(price_info["publish_time"]),
            )

            self._prices[feed_id] = price_data
            return price_data

        except Exception as e:
            logger.error(f"Failed to get price for {feed_id}: {e}")
            return None

    async def get_prices(self, feed_ids: list[str]) -> dict[str, PriceData]:
        """Get latest prices for multiple feeds."""
        try:
            params = [("ids[]", fid) for fid in feed_ids]
            resp = await self._http.get("/v2/updates/price/latest", params=params)
            resp.raise_for_status()
            data = resp.json()

            results = {}
            for parsed in data.get("parsed", []):
                feed_id = "0x" + parsed["id"]
                price_info = parsed["price"]

                symbol = next(
                    (k for k, v in self.FEEDS.items() if v == feed_id),
                    "UNKNOWN",
                )

                price_data = PriceData(
                    feed_id=feed_id,
                    symbol=symbol,
                    price=Decimal(price_info["price"]),
                    confidence=Decimal(price_info["conf"]),
                    expo=int(price_info["expo"]),
                    publish_time=datetime.fromtimestamp(price_info["publish_time"]),
                )

                results[feed_id] = price_data
                self._prices[feed_id] = price_data

            return results

        except Exception as e:
            logger.error(f"Failed to get prices: {e}")
            return {}

    async def get_crypto_prices(self) -> dict[str, Decimal]:
        """Get all tracked crypto prices as simple dict."""
        prices = await self.get_prices(list(self.FEEDS.values()))
        return {p.symbol: p.price_adjusted for p in prices.values()}

    async def start_polling(self, interval_ms: int = 500) -> None:
        """Start polling prices at regular interval."""
        self._poll_task = asyncio.create_task(self._poll_loop(interval_ms))

    async def _poll_loop(self, interval_ms: int) -> None:
        """Continuous polling loop."""
        interval_sec = interval_ms / 1000

        while self._running:
            try:
                await self.get_prices(list(self.FEEDS.values()))
            except Exception as e:
                logger.warning(f"Poll error: {e}")

            await asyncio.sleep(interval_sec)

    def get_cached_price(self, symbol: str) -> Optional[Decimal]:
        """Get cached price by symbol."""
        feed_id = self.FEEDS.get(symbol)
        if feed_id and feed_id in self._prices:
            return self._prices[feed_id].price_adjusted
        return None
