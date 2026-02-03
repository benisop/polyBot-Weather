"""
PolyBot Main Entry Point

Usage:
    python -m polybot.main          # Run bot
    python -m polybot.main --scan   # Scan only (no execution)
    python -m polybot.main --dash   # Run dashboard only
"""

import argparse
import asyncio
import signal
import sys
from decimal import Decimal
from pathlib import Path

from loguru import logger

from polybot.connectors.polymarket import PolymarketConnector
from polybot.connectors.pyth import PythConnector
from polybot.strategies.binary_arb import BinaryArbStrategy
from polybot.core.risk_manager import RiskManager, RiskLimits


def setup_logging(log_file: str = "logs/polybot.log", level: str = "INFO") -> None:
    """Configure logging."""
    # Remove default handler
    logger.remove()

    # Console output
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=level,
    )

    # File output
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
    )


def load_config() -> dict:
    """Load configuration from environment."""
    from dotenv import load_dotenv
    import os

    load_dotenv()

    return {
        "private_key": os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        "funder_address": os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
        "api_key": os.getenv("POLYMARKET_API_KEY"),
        "api_secret": os.getenv("POLYMARKET_API_SECRET"),
        "api_passphrase": os.getenv("POLYMARKET_API_PASSPHRASE"),
        "min_profit_percent": float(os.getenv("MIN_ARB_PROFIT_PERCENT", "1.5")),
        "max_position_percent": float(os.getenv("MAX_POSITION_PERCENT", "5.0")),
        "starting_capital": float(os.getenv("STARTING_CAPITAL", "1000")),
    }


async def run_scanner(connector: PolymarketConnector) -> None:
    """Run in scan-only mode (no execution)."""
    logger.info("Running in SCAN-ONLY mode - no trades will be executed")

    await connector.connect()
    await connector.fetch_markets()

    while True:
        opportunities = await connector.scan_arbitrage(Decimal("1.0"))

        if opportunities:
            logger.info(f"\n{'='*60}")
            logger.info(f"Found {len(opportunities)} arbitrage opportunities:")
            for i, opp in enumerate(opportunities[:10], 1):
                logger.info(
                    f"  {i}. {opp.market.question[:50]}... | "
                    f"Profit: {opp.net_profit_percent:.2f}% | "
                    f"Size: ${opp.max_size:.2f}"
                )
            logger.info(f"{'='*60}\n")
        else:
            logger.info("No opportunities found this scan")

        await asyncio.sleep(5)


async def run_bot(config: dict) -> None:
    """Run the full trading bot."""
    # Validate config
    if not config["private_key"] or config["private_key"] == "your_polygon_private_key_here":
        logger.error("Missing POLYMARKET_PRIVATE_KEY in .env file!")
        logger.info("Copy .env.example to .env and add your Polygon wallet private key")
        return

    if not config["funder_address"]:
        logger.error("Missing POLYMARKET_FUNDER_ADDRESS in .env file!")
        return

    # Initialize connector
    connector = PolymarketConnector(
        private_key=config["private_key"],
        funder_address=config["funder_address"],
        api_key=config.get("api_key"),
        api_secret=config.get("api_secret"),
        api_passphrase=config.get("api_passphrase"),
    )

    # Initialize risk manager
    capital = Decimal(str(config["starting_capital"]))
    risk_manager = RiskManager(
        total_capital=capital,
        limits=RiskLimits(
            max_position_percent=Decimal(str(config["max_position_percent"])),
            min_profit_threshold=Decimal(str(config["min_profit_percent"])),
        ),
    )

    # Initialize strategy
    strategy = BinaryArbStrategy(
        connector=connector,
        min_profit_percent=Decimal(str(config["min_profit_percent"])),
        max_position_percent=Decimal(str(config["max_position_percent"])),
    )

    # Connect
    await connector.connect()

    # Check balance
    balance = await connector.get_balance()
    logger.info(f"Wallet balance: ${balance:.2f} USDC")

    if balance < 10:
        logger.warning("Low balance! Fund your wallet with USDC on Polygon")

    # Start strategy
    await strategy.start(capital)

    # Keep running
    try:
        while True:
            stats = strategy.get_stats()
            logger.info(
                f"Stats | Trades: {stats['trades_executed']} | "
                f"Deployed: ${stats['deployed']:.2f} | "
                f"Opportunities: {stats['opportunities_current']}"
            )
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
        await strategy.stop()
        await connector.disconnect()


def run_dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess

    dashboard_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run(["streamlit", "run", str(dashboard_path)])


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="PolyBot - Polymarket Arbitrage Bot")
    parser.add_argument("--scan", action="store_true", help="Scan-only mode (no execution)")
    parser.add_argument("--dash", action="store_true", help="Run dashboard only")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Setup logging
    setup_logging(level="DEBUG" if args.debug else "INFO")
    logger.info("🚀 PolyBot Starting...")

    # Load config
    config = load_config()

    if args.dash:
        run_dashboard()
        return

    # Run async bot
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle shutdown
    def shutdown(sig, frame):
        logger.info("Shutdown signal received...")
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        if args.scan:
            # Scan mode doesn't need full config
            connector = PolymarketConnector(
                private_key="0x" + "0" * 64,  # Dummy key for read-only
                funder_address="0x" + "0" * 40,
            )
            loop.run_until_complete(run_scanner(connector))
        else:
            loop.run_until_complete(run_bot(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
