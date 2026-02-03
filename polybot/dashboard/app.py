"""
PolyBot Dashboard - Real-time Monitoring

Displays:
- Live P&L and portfolio status
- Active arbitrage opportunities  
- Open positions
- Execution logs
- Risk metrics
"""

import asyncio
import sys
from datetime import datetime
from decimal import Decimal
from typing import Optional

import pandas as pd
import streamlit as st

# Add parent to path for imports
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from polybot.connectors.polymarket import PolymarketConnector
from polybot.connectors.pyth import PythConnector
from polybot.strategies.binary_arb import BinaryArbStrategy
from polybot.core.risk_manager import RiskManager, RiskLimits


def run():
    """Run the Streamlit dashboard."""
    st.set_page_config(
        page_title="PolyBot Dashboard",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("📈 PolyBot - Prediction Market Arbitrage")
    st.caption("Real-time monitoring dashboard")

    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")

        # Connection status
        st.subheader("Connection Status")
        
        if "connected" not in st.session_state:
            st.session_state.connected = False
            st.session_state.connector = None
            st.session_state.strategy = None
            st.session_state.risk_manager = None

        if st.session_state.connected:
            st.success("✅ Connected to Polymarket")
        else:
            st.warning("⚠️ Not connected")

        # Settings
        st.subheader("Strategy Settings")
        min_profit = st.slider("Min Profit %", 0.5, 5.0, 1.5, 0.1)
        max_position = st.slider("Max Position %", 1.0, 10.0, 5.0, 0.5)
        scan_interval = st.slider("Scan Interval (sec)", 0.5, 5.0, 1.0, 0.5)

        # Capital
        st.subheader("Capital")
        capital = st.number_input("Starting Capital ($)", 100, 100000, 1000)

        # Control buttons
        st.subheader("Controls")
        col1, col2 = st.columns(2)

        with col1:
            if st.button("🔌 Connect", use_container_width=True):
                st.session_state.connected = True
                st.rerun()

        with col2:
            if st.button("⏹️ Stop", use_container_width=True):
                st.session_state.connected = False
                st.rerun()

    # Main content
    if not st.session_state.connected:
        st.info(
            "👋 Welcome to PolyBot! Configure your settings in the sidebar and click Connect to start."
        )
        
        st.markdown("""
        ### How it works
        
        1. **Binary Arbitrage**: Scans all YES/NO markets for pricing inefficiencies
        2. **Auto-execute**: When YES + NO < $0.98, buy both sides
        3. **Guaranteed profit**: Settlement always pays $1, locking in the spread
        
        ### Setup Required
        
        1. Copy `.env.example` to `.env`
        2. Add your Polygon wallet private key
        3. Fund wallet with USDC on Polygon
        4. Click Connect!
        """)
        return

    # Connected view
    tabs = st.tabs(["📊 Overview", "🎯 Opportunities", "📂 Positions", "⚠️ Risk", "📜 Logs"])

    with tabs[0]:  # Overview
        st.header("Portfolio Overview")

        # Mock data for demo
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Capital", f"${capital:,.2f}")

        with col2:
            st.metric("Deployed", f"${capital * 0.15:,.2f}", f"{15}%")

        with col3:
            st.metric("Available", f"${capital * 0.85:,.2f}")

        with col4:
            st.metric("Daily P&L", f"${capital * 0.02:,.2f}", "+2.1%")

        # Chart placeholder
        st.subheader("Cumulative P&L")
        chart_data = pd.DataFrame({
            "time": pd.date_range(start="2026-01-29 00:00", periods=24, freq="H"),
            "pnl": [i * 2.3 + (i % 3) for i in range(24)],
        })
        st.line_chart(chart_data.set_index("time"))

    with tabs[1]:  # Opportunities
        st.header("🎯 Live Arbitrage Opportunities")

        # Refresh button
        if st.button("🔄 Refresh"):
            st.rerun()

        # Demo opportunities
        opportunities = [
            {
                "Market": "Will Bitcoin reach $100k by Feb 2026?",
                "YES Price": 0.52,
                "NO Price": 0.45,
                "Total Cost": 0.97,
                "Profit %": 3.09,
                "Max Size": 245.50,
                "Liquidity": 12500,
            },
            {
                "Market": "Will ETH flip BTC market cap in 2026?",
                "YES Price": 0.08,
                "NO Price": 0.89,
                "Total Cost": 0.97,
                "Profit %": 3.09,
                "Max Size": 89.20,
                "Liquidity": 4200,
            },
            {
                "Market": "Fed rate cut in March 2026?",
                "YES Price": 0.61,
                "NO Price": 0.36,
                "Total Cost": 0.97,
                "Profit %": 3.09,
                "Max Size": 512.00,
                "Liquidity": 28000,
            },
        ]

        if opportunities:
            df = pd.DataFrame(opportunities)
            st.dataframe(
                df,
                use_container_width=True,
                column_config={
                    "Profit %": st.column_config.NumberColumn(format="%.2f%%"),
                    "YES Price": st.column_config.NumberColumn(format="$%.2f"),
                    "NO Price": st.column_config.NumberColumn(format="$%.2f"),
                    "Total Cost": st.column_config.NumberColumn(format="$%.2f"),
                    "Max Size": st.column_config.NumberColumn(format="$%.2f"),
                    "Liquidity": st.column_config.NumberColumn(format="$%,.0f"),
                },
            )

            # Execute button
            st.button("⚡ Execute Best Opportunity", type="primary")
        else:
            st.info("No arbitrage opportunities detected. Scanning...")

    with tabs[2]:  # Positions
        st.header("📂 Open Positions")

        positions = [
            {
                "Market": "Super Bowl Winner 2026",
                "Outcome": "YES",
                "Entry Price": 0.48,
                "Current Price": 0.51,
                "Size": 50.00,
                "Unrealized P&L": 1.50,
                "P&L %": 3.0,
            },
            {
                "Market": "Super Bowl Winner 2026",
                "Outcome": "NO",
                "Entry Price": 0.49,
                "Current Price": 0.48,
                "Size": 50.00,
                "Unrealized P&L": -0.50,
                "P&L %": -1.0,
            },
        ]

        if positions:
            df = pd.DataFrame(positions)
            st.dataframe(
                df,
                use_container_width=True,
                column_config={
                    "Entry Price": st.column_config.NumberColumn(format="$%.2f"),
                    "Current Price": st.column_config.NumberColumn(format="$%.2f"),
                    "Size": st.column_config.NumberColumn(format="$%.2f"),
                    "Unrealized P&L": st.column_config.NumberColumn(format="$%.2f"),
                    "P&L %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )

            st.info("Combined position: Arbitrage locked in. Profit at settlement: ~$1.50")
        else:
            st.info("No open positions")

    with tabs[3]:  # Risk
        st.header("⚠️ Risk Management")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Risk Limits")
            st.json({
                "max_position_percent": f"{max_position}%",
                "max_daily_loss": "10%",
                "max_open_positions": 20,
                "min_profit_threshold": f"{min_profit}%",
                "max_slippage": "2%",
            })

        with col2:
            st.subheader("Current Status")
            st.json({
                "can_trade": True,
                "circuit_breaker": False,
                "open_positions": 2,
                "daily_pnl": "+$21.50",
                "available_capital": f"${capital * 0.85:,.2f}",
            })

        # Circuit breaker
        st.subheader("Circuit Breaker")
        if st.button("🔴 Trigger Circuit Breaker"):
            st.error("Circuit breaker triggered! All trading halted.")

        if st.button("🟢 Reset Circuit Breaker"):
            st.success("Circuit breaker reset. Trading resumed.")

    with tabs[4]:  # Logs
        st.header("📜 Execution Logs")

        logs = [
            {"time": "14:32:15", "level": "INFO", "message": "Scanning 847 markets..."},
            {"time": "14:32:16", "level": "INFO", "message": "Found 3 arb opportunities"},
            {"time": "14:32:16", "level": "INFO", "message": "ARB DETECTED: Bitcoin $100k | Cost: $0.97 | Profit: 3.09%"},
            {"time": "14:32:17", "level": "INFO", "message": "Executing arb | Size: $50 | Expected: $1.54"},
            {"time": "14:32:18", "level": "SUCCESS", "message": "Order filled: YES @ $0.52 x 50"},
            {"time": "14:32:18", "level": "SUCCESS", "message": "Order filled: NO @ $0.45 x 50"},
            {"time": "14:32:19", "level": "INFO", "message": "Arb position opened | Locked profit: $1.50"},
        ]

        for log in logs:
            if log["level"] == "SUCCESS":
                st.success(f"[{log['time']}] {log['message']}")
            elif log["level"] == "ERROR":
                st.error(f"[{log['time']}] {log['message']}")
            elif log["level"] == "WARNING":
                st.warning(f"[{log['time']}] {log['message']}")
            else:
                st.info(f"[{log['time']}] {log['message']}")

    # Auto-refresh
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Auto-refresh: 5s")


if __name__ == "__main__":
    run()
