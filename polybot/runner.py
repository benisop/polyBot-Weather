#!/usr/bin/env python3
"""
PolyBot Multi-Strategy Runner

Runs all enabled strategies (weather, crypto, copy trading) with
proper coordination, risk management, and data persistence.

Designed for latency-tolerant execution (Mac Mini friendly).
"""

import asyncio
import signal
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from loguru import logger

from polybot.config import get_settings
from polybot.connectors.polymarket import PolymarketConnector
from polybot.connectors.noaa import NOAAConnector
from polybot.connectors.pyth import PythConnector
from polybot.connectors.copy_trading import CopyTradingConnector, discover_top_traders
from polybot.core.risk_manager import RiskManager, RiskLimits
from polybot.core.simulation import SimulationEngine
from polybot.core.datastore import DataStore, create_datastore
from polybot.strategies.weather_v2 import WeatherStrategyV2
from polybot.strategies.crypto import CryptoStrategy
from polybot.strategies.binary_arb import BinaryArbStrategy


class PolyBotRunner:
    """
    Unified multi-strategy runner.
    
    Coordinates all strategies with shared:
    - Risk management
    - Data persistence
    - Position tracking
    - Performance monitoring
    """

    def __init__(
        self,
        simulation_mode: bool = True,
        starting_capital: Decimal = Decimal("1000"),
    ):
        self.settings = get_settings()
        self.simulation_mode = simulation_mode
        self.starting_capital = starting_capital
        
        # Connectors
        self.polymarket: PolymarketConnector = None
        self.noaa: NOAAConnector = None
        self.pyth: PythConnector = None
        self.copy_connector: CopyTradingConnector = None
        
        # Core components
        self.risk_manager: RiskManager = None
        self.simulation: SimulationEngine = None
        self.datastore: DataStore = None
        
        # Strategies
        self.weather_strategy: WeatherStrategyV2 = None
        self.crypto_strategy: CryptoStrategy = None
        self.arb_strategy: BinaryArbStrategy = None
        
        # State
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("🚀 Initializing PolyBot Multi-Strategy Runner...")
        
        # Initialize datastore
        self.datastore = await create_datastore(str(self.settings.db_path))
        logger.info("📊 Datastore connected")
        
        # Initialize Polymarket connector
        self.polymarket = PolymarketConnector(
            private_key=self.settings.polymarket.private_key.get_secret_value(),
            funder_address=self.settings.polymarket.funder_address,
            api_key=self.settings.polymarket.api_key,
            api_secret=self.settings.polymarket.api_secret.get_secret_value() if self.settings.polymarket.api_secret else None,
            api_passphrase=self.settings.polymarket.api_passphrase.get_secret_value() if self.settings.polymarket.api_passphrase else None,
        )
        await self.polymarket.connect()
        logger.info("🔗 Polymarket connector ready")
        
        # Initialize NOAA connector
        self.noaa = NOAAConnector()
        logger.info("🌦️ NOAA connector ready")
        
        # Initialize Pyth connector
        self.pyth = PythConnector(hermes_url=self.settings.pyth.hermes_url)
        await self.pyth.connect()
        await self.pyth.start_polling(interval_ms=self.settings.pyth.poll_interval_ms)
        logger.info("₿ Pyth connector ready")
        
        # Initialize risk manager
        self.risk_manager = RiskManager(
            total_capital=self.starting_capital,
            limits=RiskLimits(
                max_position_percent=self.settings.risk.max_position_percent,
                max_slippage_percent=self.settings.risk.max_slippage_percent,
                max_daily_loss_percent=self.settings.risk.circuit_breaker_loss_percent,
                max_open_positions=self.settings.risk.max_open_positions,
                min_profit_threshold=self.settings.risk.min_arb_profit_percent,
            )
        )
        
        # Initialize simulation engine if in simulation mode
        if self.simulation_mode:
            self.simulation = SimulationEngine(
                starting_balance=self.starting_capital,
                fee_percent=Decimal("0"),  # Polymarket is 0% fees
                slippage_percent=Decimal("0.5"),
            )
            logger.info(f"🎮 Simulation mode: ${self.starting_capital} starting capital")
        
        # Initialize strategies
        if self.settings.weather.enabled:
            self.weather_strategy = WeatherStrategyV2(
                polymarket=self.polymarket,
                noaa=self.noaa,
                simulation=self.simulation,
                min_edge_percent=Decimal(str(self.settings.weather.min_edge_percent)),
                min_zscore=self.settings.weather.min_zscore,
                max_kelly_fraction=self.settings.weather.max_kelly_fraction,
            )
            logger.info("🌦️ Weather Strategy V2 enabled")
        
        if self.settings.crypto.enabled:
            self.crypto_strategy = CryptoStrategy(
                polymarket=self.polymarket,
                pyth=self.pyth,
                simulation=self.simulation,
                min_edge_percent=Decimal(str(self.settings.crypto.min_edge_percent)),
                min_zscore=self.settings.crypto.min_zscore,
                min_days_to_expiry=self.settings.crypto.min_days_to_expiry,
                max_days_to_expiry=self.settings.crypto.max_days_to_expiry,
            )
            logger.info("₿ Crypto Strategy enabled")
        
        if self.settings.copy_trading.enabled:
            self.copy_connector = CopyTradingConnector(
                rpc_url=self.settings.polymarket.rpc_url,
                copy_delay_seconds=self.settings.copy_trading.copy_delay_seconds,
                max_copy_size=Decimal(str(self.settings.copy_trading.max_copy_size)),
                copy_fraction=self.settings.copy_trading.copy_fraction,
                min_trader_win_rate=self.settings.copy_trading.min_trader_win_rate,
            )
            await self.copy_connector.connect()
            self.copy_connector.add_default_traders()
            logger.info("👥 Copy Trading enabled")
        
        logger.info("✅ All components initialized")

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("🛑 Shutting down PolyBot...")
        self._running = False
        self._shutdown_event.set()
        
        # Disconnect all connectors
        if self.polymarket:
            await self.polymarket.disconnect()
        if self.pyth:
            await self.pyth.disconnect()
        if self.copy_connector:
            await self.copy_connector.disconnect()
        if self.datastore:
            await self.datastore.disconnect()
        
        # Print final stats
        if self.simulation:
            summary = self.simulation.get_summary()
            logger.info("=" * 60)
            logger.info("📊 FINAL SIMULATION RESULTS")
            logger.info("=" * 60)
            logger.info(f"Starting Balance: ${summary['starting_balance']:.2f}")
            logger.info(f"Final Balance: ${summary['current_balance']:.2f}")
            logger.info(f"Total P&L: ${summary['total_pnl']:.2f} ({summary['return_percent']:.1f}%)")
            logger.info(f"Trades Executed: {summary['trades_executed']}")
            logger.info(f"Win Rate: {summary['win_rate']:.1%}")
            logger.info("=" * 60)
        
        logger.info("👋 PolyBot shutdown complete")

    async def run_weather_scan(self) -> None:
        """Run weather strategy scan and execute signals."""
        if not self.weather_strategy:
            return
        
        try:
            signals = await self.weather_strategy.scan_markets()
            
            for signal in signals[:3]:  # Execute top 3 signals
                if signal.confidence in ("HIGH", "MEDIUM"):
                    # Check risk limits
                    allowed, reason, size = self.risk_manager.validate_trade(
                        requested_size=self.starting_capital * Decimal(str(signal.kelly_fraction)),
                        expected_profit_percent=float(signal.edge) * 100,
                        market_liquidity=signal.market.liquidity,
                    )
                    
                    if allowed:
                        order = await self.weather_strategy.execute_signal(
                            signal=signal,
                            capital=self.starting_capital,
                        )
                        
                        if order:
                            # Save to datastore
                            signal_id = f"weather_{datetime.utcnow().timestamp()}"
                            await self.datastore.save_signal(
                                signal_id=signal_id,
                                strategy="weather_v2",
                                market_id=signal.market.id,
                                market_question=signal.market.question,
                                market_price=signal.market_prob,
                                forecast_price=signal.forecast_prob,
                                edge=signal.edge,
                                recommended_side=signal.recommended_side.value,
                                edge_zscore=signal.edge_zscore,
                                confidence=signal.confidence,
                                kelly_fraction=signal.kelly_fraction,
                                reasoning=signal.reasoning,
                            )
                    else:
                        logger.debug(f"Trade rejected: {reason}")
            
            # Log stats
            stats = self.weather_strategy.get_stats()
            logger.info(f"🌦️ Weather: {stats['total_signals']} signals, {stats['high_confidence']} high conf")
            
        except Exception as e:
            logger.error(f"Weather scan failed: {e}")

    async def run_crypto_scan(self) -> None:
        """Run crypto strategy scan and execute signals."""
        if not self.crypto_strategy:
            return
        
        try:
            signals = await self.crypto_strategy.scan_markets()
            
            for signal in signals[:3]:  # Execute top 3 signals
                if signal.confidence in ("HIGH", "MEDIUM"):
                    allowed, reason, size = self.risk_manager.validate_trade(
                        requested_size=self.starting_capital * Decimal(str(signal.kelly_fraction)),
                        expected_profit_percent=float(signal.edge) * 100,
                        market_liquidity=signal.market.liquidity,
                    )
                    
                    if allowed:
                        order = await self.crypto_strategy.execute_signal(
                            signal=signal,
                            capital=self.starting_capital,
                        )
                        
                        if order:
                            signal_id = f"crypto_{datetime.utcnow().timestamp()}"
                            await self.datastore.save_signal(
                                signal_id=signal_id,
                                strategy="crypto",
                                market_id=signal.market.id,
                                market_question=signal.market.question,
                                market_price=signal.market_prob,
                                forecast_price=signal.forecast_prob,
                                edge=signal.edge,
                                recommended_side=signal.recommended_side.value,
                                edge_zscore=signal.edge_zscore,
                                confidence=signal.confidence,
                                kelly_fraction=signal.kelly_fraction,
                                reasoning=signal.reasoning,
                            )
                    else:
                        logger.debug(f"Trade rejected: {reason}")
            
            stats = self.crypto_strategy.get_stats()
            logger.info(f"₿ Crypto: {stats['total_signals']} signals, {stats['high_confidence']} high conf")
            
        except Exception as e:
            logger.error(f"Crypto scan failed: {e}")

    async def run_arb_scan(self) -> None:
        """Run binary arbitrage scan."""
        try:
            opportunities = await self.polymarket.scan_arbitrage_fast(
                min_profit_percent=Decimal(str(self.settings.risk.min_arb_profit_percent))
            )
            
            for opp in opportunities[:5]:
                if self.simulation:
                    self.simulation.simulate_arbitrage(opp, size=min(opp.max_size, Decimal("50")))
                
                await self.datastore.save_arb_opportunity(opp)
            
            logger.info(f"📈 Arbitrage: {len(opportunities)} opportunities found")
            
        except Exception as e:
            logger.error(f"Arb scan failed: {e}")

    async def save_market_snapshots(self) -> None:
        """Periodically save market snapshots for historical analysis."""
        try:
            markets = await self.polymarket.fetch_markets()
            await self.datastore.save_market_snapshots(markets)
            logger.debug(f"Saved {len(markets)} market snapshots")
        except Exception as e:
            logger.error(f"Failed to save snapshots: {e}")

    async def run_main_loop(
        self,
        scan_interval_minutes: int = 5,
        snapshot_interval_minutes: int = 15,
    ) -> None:
        """
        Main trading loop.
        
        Runs all strategies on a regular interval.
        """
        self._running = True
        last_snapshot = datetime.min
        
        logger.info(f"🔄 Starting main loop (scan every {scan_interval_minutes}m)")
        
        while self._running:
            try:
                loop_start = datetime.utcnow()
                
                # Run all strategy scans
                await asyncio.gather(
                    self.run_weather_scan(),
                    self.run_crypto_scan(),
                    self.run_arb_scan(),
                    return_exceptions=True,
                )
                
                # Periodic snapshots
                if (loop_start - last_snapshot).total_seconds() > snapshot_interval_minutes * 60:
                    await self.save_market_snapshots()
                    last_snapshot = loop_start
                
                # Update daily performance
                await self.datastore.update_daily_performance()
                
                # Show current status
                if self.simulation:
                    summary = self.simulation.get_summary()
                    logger.info(
                        f"💰 Balance: ${summary['current_balance']:.2f} | "
                        f"P&L: ${summary['total_pnl']:.2f} ({summary['return_percent']:.1f}%) | "
                        f"Trades: {summary['trades_executed']}"
                    )
                
                # Wait for next scan
                elapsed = (datetime.utcnow() - loop_start).total_seconds()
                sleep_time = max(0, scan_interval_minutes * 60 - elapsed)
                
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=sleep_time,
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue loop
                
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)  # Back off on error

    async def run(
        self,
        duration_minutes: Optional[int] = None,
        scan_interval_minutes: int = 5,
    ) -> None:
        """
        Run the bot for a specified duration (or indefinitely).
        """
        await self.initialize()
        
        # Setup signal handlers for graceful shutdown
        def handle_shutdown(signum, frame):
            logger.info("Received shutdown signal")
            asyncio.create_task(self.shutdown())
        
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
        
        if duration_minutes:
            logger.info(f"Running for {duration_minutes} minutes...")
            asyncio.create_task(self._auto_shutdown(duration_minutes))
        
        try:
            await self.run_main_loop(scan_interval_minutes=scan_interval_minutes)
        finally:
            await self.shutdown()

    async def _auto_shutdown(self, minutes: int) -> None:
        """Auto-shutdown after specified duration."""
        await asyncio.sleep(minutes * 60)
        await self.shutdown()


async def main():
    """Entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="PolyBot Multi-Strategy Runner")
    parser.add_argument("--live", action="store_true", help="Run in live mode (real trades)")
    parser.add_argument("--capital", type=float, default=1000, help="Starting capital for simulation")
    parser.add_argument("--duration", type=int, help="Run duration in minutes (default: indefinite)")
    parser.add_argument("--interval", type=int, default=5, help="Scan interval in minutes")
    
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add("logs/polybot.log", rotation="10 MB", level="DEBUG")
    
    runner = PolyBotRunner(
        simulation_mode=not args.live,
        starting_capital=Decimal(str(args.capital)),
    )
    
    await runner.run(
        duration_minutes=args.duration,
        scan_interval_minutes=args.interval,
    )


if __name__ == "__main__":
    asyncio.run(main())
