#!/usr/bin/env python3
"""
Simulation Runner - Test bot strategies without real money.

Usage:
    python run_simulation.py                    # Run binary arb simulation
    python run_simulation.py --weather          # Run weather strategy
    python run_simulation.py --both             # Run both strategies
    python run_simulation.py --capital 5000     # Start with $5000
"""

import argparse
import asyncio
from decimal import Decimal

from loguru import logger

from polybot.connectors.polymarket import PolymarketConnector
from polybot.connectors.noaa import NOAAConnector
from polybot.core.simulation import SimulationEngine
from polybot.strategies.binary_arb import BinaryArbStrategy
from polybot.strategies.weather import WeatherStrategy
from polybot.models import OutcomeType


async def run_demo_arbs(sim: SimulationEngine, markets: list):
    """
    Run demo arbitrage simulation with synthetic opportunities.
    
    This demonstrates how the bot would work when real arbs appear.
    """
    from polybot.models import Market, ArbOpportunity
    
    logger.info("📊 DEMO MODE: Simulating realistic arbitrage scenarios\n")
    
    demo_scenarios = [
        {"yes_price": Decimal("0.48"), "no_price": Decimal("0.49"), "desc": "Tight arb (3% profit)"},
        {"yes_price": Decimal("0.45"), "no_price": Decimal("0.50"), "desc": "Good arb (5% profit)"},
        {"yes_price": Decimal("0.40"), "no_price": Decimal("0.52"), "desc": "Great arb (8% profit)"},
        {"yes_price": Decimal("0.55"), "no_price": Decimal("0.42"), "desc": "Reversed arb (3% profit)"},
    ]
    
    for i, (market, scenario) in enumerate(zip(markets[:4], demo_scenarios)):
        # Create synthetic opportunity
        yes_price = scenario["yes_price"]
        no_price = scenario["no_price"]
        total_cost = yes_price + no_price
        gross_profit = Decimal("1") - total_cost
        estimated_fees = gross_profit * Decimal("0.02")
        net_profit = gross_profit - estimated_fees
        net_profit_percent = (net_profit / total_cost) * 100
        
        # Update market with synthetic prices
        market.yes_price = yes_price
        market.no_price = no_price
        market.yes_ask_size = Decimal("100")
        market.no_ask_size = Decimal("100")
        
        opp = ArbOpportunity(
            market=market,
            yes_buy_price=yes_price,
            no_buy_price=no_price,
            total_cost=total_cost,
            gross_profit=gross_profit,
            estimated_fees=estimated_fees,
            net_profit=net_profit,
            net_profit_percent=net_profit_percent,
            max_size=Decimal("100"),
        )
        
        logger.info(
            f"🎯 Demo Arb {i+1}: {market.question[:40]}..."
        )
        logger.info(
            f"   {scenario['desc']} | YES: ${yes_price} + NO: ${no_price} = ${total_cost}"
        )
        logger.info(
            f"   Net profit: {net_profit_percent:.2f}% | Executing $50 position..."
        )
        
        # Execute simulation
        sim.simulate_arbitrage(opp, Decimal("50"))
        logger.info("")
    
    # Simulate settlement for one arb pair
    logger.info("⏰ Simulating market settlement...")
    if markets:
        settled_market = markets[0]
        profit = sim.settle_arb_pair(settled_market.id, OutcomeType.YES)
        logger.info(f"   Settlement profit: ${profit:.4f}")



async def run_binary_arb_simulation(sim: SimulationEngine, duration_seconds: int = 60):
    """Run binary arbitrage simulation."""
    logger.info("=" * 60)
    logger.info("🎮 BINARY ARBITRAGE SIMULATION")
    logger.info("=" * 60)
    
    # Create connector (read-only, no real wallet needed)
    connector = PolymarketConnector(
        private_key="0x" + "0" * 64,
        funder_address="0x" + "0" * 40,
    )
    
    await connector.connect()
    
    # Fetch markets
    markets = await connector.fetch_markets()
    logger.info(f"Loaded {len(markets)} markets")
    
    # Scan for real opportunities first
    opportunities = await connector.scan_arbitrage_fast(Decimal("0.5"))
    
    if opportunities:
        logger.info(f"\nFound {len(opportunities)} real arbitrage opportunities!")
        for i, opp in enumerate(opportunities[:5], 1):
            logger.info(
                f"  {i}. {opp.market.question[:50]}... | "
                f"Cost: ${opp.total_cost:.4f} | Profit: {opp.net_profit_percent:.2f}%"
            )
            if i <= 2:
                size = min(Decimal("50"), opp.max_size)
                if size > 0:
                    sim.simulate_arbitrage(opp, size)
    else:
        logger.info("\nNo real arb opportunities found (market is well-arbitraged)")
        logger.info("Running demo with synthetic opportunities...\n")
        
        # Create synthetic demo opportunities from real markets
        await run_demo_arbs(sim, list(connector._markets.values())[:10])
    
    await connector.disconnect()
    
    # Show summary
    logger.info("\n" + "=" * 60)
    logger.info("BINARY ARB SIMULATION RESULTS")
    logger.info("=" * 60)
    
    summary = sim.get_summary()
    for key, value in summary.items():
        if isinstance(value, float):
            logger.info(f"  {key}: {value:.4f}")
        else:
            logger.info(f"  {key}: {value}")


async def run_weather_simulation(sim: SimulationEngine):
    """Run weather strategy simulation."""
    logger.info("=" * 60)
    logger.info("🌦️ WEATHER STRATEGY SIMULATION")
    logger.info("=" * 60)
    
    # Create connectors
    polymarket = PolymarketConnector(
        private_key="0x" + "0" * 64,
        funder_address="0x" + "0" * 40,
    )
    noaa = NOAAConnector()
    
    await polymarket.connect()
    await noaa.connect()
    
    # Create weather strategy with simulation
    strategy = WeatherStrategy(
        polymarket=polymarket,
        noaa=noaa,
        simulation=sim,
        min_edge_percent=Decimal("15"),
    )
    
    # Scan for weather opportunities
    opportunities = await strategy.scan_markets()
    
    if opportunities:
        logger.info(f"\nFound {len(opportunities)} weather opportunities:")
        
        for i, opp in enumerate(opportunities[:10], 1):
            logger.info(
                f"  {i}. [{opp.confidence}] {opp.market.question[:45]}..."
            )
            logger.info(
                f"      Market: {opp.market_prob:.0%} vs Forecast: {opp.forecast_prob:.0%} | "
                f"Edge: {opp.edge:.1%} | Action: Buy {opp.recommended_side.value}"
            )
            logger.info(f"      Reason: {opp.reasoning}")
            
            # Execute high confidence opportunities
            if opp.confidence == "HIGH":
                await strategy.execute_opportunity(opp, Decimal("25"))
    else:
        logger.info("No weather opportunities found (market may not have weather markets)")
        
        # Demo with synthetic data
        logger.info("\n📊 Demonstrating with test weather data...")
        await demo_weather_forecast(noaa)
    
    await polymarket.disconnect()
    await noaa.disconnect()
    
    # Show summary
    logger.info("\n" + "=" * 60)
    logger.info("WEATHER STRATEGY SIMULATION RESULTS")
    logger.info("=" * 60)
    
    summary = sim.get_summary()
    for key, value in summary.items():
        if isinstance(value, float):
            logger.info(f"  {key}: {value:.4f}")
        else:
            logger.info(f"  {key}: {value}")


async def demo_weather_forecast(noaa: NOAAConnector):
    """Demo NOAA weather data for major cities."""
    logger.info("\n🌍 Fetching real weather data from NOAA...")
    
    cities = ["New York", "Los Angeles", "Chicago", "Miami", "Denver"]
    
    for city in cities:
        try:
            daily = await noaa.get_daily_summary(city)
            if daily:
                tomorrow = daily[0]
                logger.info(
                    f"  {city}: High {tomorrow.high_temp}°F, Low {tomorrow.low_temp}°F, "
                    f"Precip: {tomorrow.precipitation_probability}%, {tomorrow.conditions}"
                )
                
                # Check rain/snow
                will_rain, rain_prob = await noaa.will_it_rain(city)
                will_snow, snow_prob = await noaa.will_it_snow(city)
                
                if will_rain:
                    logger.info(f"    🌧️ Rain likely ({rain_prob}%)")
                if will_snow:
                    logger.info(f"    ❄️ Snow likely ({snow_prob}%)")
                    
        except Exception as e:
            logger.warning(f"  {city}: Failed to fetch - {e}")
        
        await asyncio.sleep(0.5)  # Rate limiting


async def main():
    parser = argparse.ArgumentParser(description="PolyBot Simulation Runner")
    parser.add_argument("--weather", action="store_true", help="Run weather strategy")
    parser.add_argument("--both", action="store_true", help="Run both strategies")
    parser.add_argument("--capital", type=float, default=1000, help="Starting capital")
    parser.add_argument("--duration", type=int, default=30, help="Simulation duration (seconds)")
    args = parser.parse_args()
    
    # Setup logging
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )
    
    logger.info("🚀 PolyBot Simulation Starting...")
    logger.info(f"💰 Starting capital: ${args.capital}")
    
    # Create simulation engine
    sim = SimulationEngine(
        initial_balance=Decimal(str(args.capital)),
        fee_percent=Decimal("0.02"),
        slippage_percent=Decimal("0.5"),
    )
    
    try:
        if args.weather:
            await run_weather_simulation(sim)
        elif args.both:
            await run_binary_arb_simulation(sim, args.duration)
            await run_weather_simulation(sim)
        else:
            await run_binary_arb_simulation(sim, args.duration)
        
        # Final summary
        logger.info("\n" + "=" * 60)
        logger.info("📊 FINAL SIMULATION SUMMARY")
        logger.info("=" * 60)
        
        summary = sim.get_summary()
        logger.info(f"  Initial Balance:  ${summary['initial_balance']:.2f}")
        logger.info(f"  Final Balance:    ${summary['current_balance']:.2f}")
        logger.info(f"  Portfolio Value:  ${summary['portfolio_value']:.2f}")
        logger.info(f"  Total Trades:     {summary['total_trades']}")
        logger.info(f"  Open Positions:   {summary['open_positions']}")
        logger.info(f"  Arb Pairs:        {summary['arb_pairs_executed']}")
        logger.info(f"  Fees Paid:        ${summary['total_fees_paid']:.4f}")
        logger.info(f"  Realized P&L:     ${summary['realized_pnl']:.4f}")
        logger.info(f"  Return:           {summary['return_percent']:.2f}%")
        
        # Trade history
        trades = sim.get_trade_history()
        if trades:
            logger.info(f"\n📝 Trade History ({len(trades)} trades):")
            for t in trades[-10:]:  # Last 10 trades
                logger.info(
                    f"  [{t['timestamp'][:19]}] {t['side']} {t['size']:.2f} "
                    f"{t['outcome']} @ ${t['price']:.4f} | {t['market'][:30]}..."
                )
        
        # Open positions
        positions = sim.get_positions()
        if positions:
            logger.info(f"\n📂 Open Positions ({len(positions)}):")
            for p in positions:
                logger.info(
                    f"  {p['market'][:30]}... | {p['outcome']} | "
                    f"Size: {p['size']:.2f} @ ${p['avg_price']:.4f} | "
                    f"Value: ${p['market_value']:.2f}"
                )
        
    except KeyboardInterrupt:
        logger.info("\nSimulation interrupted by user")
    except Exception as e:
        logger.error(f"Simulation error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
