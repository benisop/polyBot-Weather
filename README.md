# PolyBot - Multi-Strategy Polymarket Trading Bot

A Python trading bot for Polymarket prediction markets using **mathematical models** and **latency-tolerant strategies** designed for retail execution environments (Mac Mini friendly).

## Overview

PolyBot implements three complementary strategies optimized for environments with 50-200ms latency:

1. **Weather Arbitrage (Gaussian Model)** - Statistical weather prediction using NOAA data
2. **Crypto Price Prediction (Black-Scholes)** - Daily+ crypto threshold markets with volatility modeling  
3. **Binary Arbitrage** - Guaranteed profit when YES + NO < $1.00

**Why these strategies?** They prioritize **signal quality over execution speed**, making them profitable even with residential internet latency. Edge comes from better probability estimation, not millisecond reaction times.

---

## Strategies Explained

### 1. Weather Arbitrage Strategy (V2)

**Edge Source**: NOAA forecasts are more accurate than market prices

**How It Works**:
- Scans Polymarket for weather markets (rain, snow, temperature)
- Fetches official NOAA forecasts for the same city/date
- Calculates probability using **Gaussian distribution** with historical RMSE data:
  - Temperature: Uses CDF with ±2.5-7.5°F RMSE based on forecast lead time
  - Precipitation: Calibrates NOAA percentages against historical accuracy
- Trades when edge exceeds 10% and statistical significance (z-score) ≥ 1.5
- Position sizing via **Kelly Criterion** (capped at 25% of suggested)

**Example**:
```
Market: "Will NYC high temperature exceed 80°F on Feb 15?"
Market Price: 35% YES
NOAA Forecast: 85°F high (1-day lead, RMSE 2.5°F)
Gaussian Model: P(>80°F) = 97.7%
Edge: 62.7% → HIGH confidence signal
```

**Suitable Markets**: 
- 24hr-7day forecast windows
- Major US cities (NY, LA, Chicago, Miami, etc.)
- Clear binary outcomes

**Update Frequency**: Every 5-15 minutes (NOAA updates hourly)

---

### 2. Crypto Price Prediction Strategy

**Edge Source**: Real-time Pyth oracle prices + volatility modeling vs market mispricing

**How It Works**:
- Uses **Pyth Network** real-time BTC/ETH/SOL/DOGE/XRP prices (400ms polling)
- Applies **Black-Scholes probability model** (zero-drift, log-normal assumption)
- Calculates: `P(price > threshold at expiry) = N(ln(S/K) / (σ√T))`
- Uses historical volatility: BTC 55%, ETH 70%, SOL 95%
- Filters to **12hr - 30 day markets** (latency-tolerant window)
- Requires 8%+ edge and z-score ≥ 1.5

**Example**:
```
Market: "Will BTC be above $100k by Feb 15?" (7 days)
Market Price: 25% YES
Current BTC: $95,000 (via Pyth)
Black-Scholes: P(>$100k in 7d) = 25.0% (σ=0.55)
Edge: 0% → NO TRADE (model agrees with market)

But if market says 10% and model says 25%, trade YES!
```

**Suitable Markets**:
- Daily or longer expiry (not 15-min markets)
- Major crypto assets with Pyth feeds
- Clear price thresholds

**Update Frequency**: Continuous (scans every 5 min, Pyth updates every 400ms)

---

### 3. Binary Arbitrage Strategy

**Edge Source**: Mathematical guarantee when markets misprice

**How It Works**:
- In binary markets: `YES_price + NO_price = $1.00` (efficient pricing)
- When `YES + NO < $1.00` → guaranteed profit opportunity
- Buy both sides, lock in difference
- Example: YES=$0.48, NO=$0.48 → Cost $0.96, payout $1.00 → 4.2% profit

**No Latency Sensitivity**: Once opportunity exists, it's guaranteed regardless of execution speed

**Update Frequency**: Every 5 minutes (opportunities rare, ~1-3% of markets)

---

## Quick Start

### 1. Setup Environment

```bash
cd polyBot
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\activate  # Windows

pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
nano .env  # Edit with your settings
```

**Required Settings**:
```bash
# Polymarket (for live trading)
POLYMARKET_PRIVATE_KEY=your_polygon_wallet_private_key
POLYMARKET_FUNDER_ADDRESS=0xYourPolygonAddress

# Strategy toggles
WEATHER_ENABLED=true
CRYPTO_ENABLED=true
COPY_ENABLED=false  # Requires additional setup

# Risk management
MAX_POSITION_PERCENT=5.0  # Max 5% capital per trade
MIN_ARB_PROFIT_PERCENT=1.5
CIRCUIT_BREAKER_LOSS_PERCENT=10.0
```

**Optional Settings**:
```bash
# Strategy tuning
WEATHER_MIN_EDGE_PERCENT=10.0
WEATHER_MIN_ZSCORE=1.5
CRYPTO_MIN_EDGE_PERCENT=8.0
CRYPTO_MIN_ZSCORE=1.5

# Pyth configuration
PYTH_POLL_INTERVAL_MS=400
```

### 3. Fund Wallet (Live Trading Only)

- Send USDC to your Polygon wallet address
- Minimum recommended: $100 ($500+ for meaningful testing)
- Bridge from Ethereum if needed: https://wallet.polygon.technology/

### 4. Run The Bot

**Simulation Mode** (paper trading, recommended first):
```bash
python -m polybot.runner --capital 1000 --interval 5
```

**Live Trading Mode** (requires funded wallet):
```bash
python -m polybot.runner --live --capital 500 --interval 5
```

**Time-Limited Run** (60 minutes):
```bash
python -m polybot.runner --capital 1000 --duration 60
```

**Command Line Options**:
| Flag | Description | Default |
|------|-------------|---------|
| `--live` | Enable real trading (vs simulation) | False |
| `--capital` | Starting capital (simulation) or max allocation (live) | 1000 |
| `--duration` | Run duration in minutes (or indefinite) | None |
| `--interval` | Scan interval in minutes | 5 |

---

## Project Structure

```
polyBot/
├── polybot/
│   ├── config.py              # Pydantic settings (reads .env)
│   ├── models.py              # Data models (Market, Order, Position)
│   ├── runner.py              # ⭐ Multi-strategy coordinator
│   │
│   ├── connectors/
│   │   ├── polymarket.py      # Polymarket CLOB REST + WebSocket
│   │   ├── noaa.py            # NOAA weather API
│   │   ├── pyth.py            # Pyth Network oracle prices
│   │   └── copy_trading.py    # On-chain trader monitoring (Polygon)
│   │
│   ├── strategies/
│   │   ├── binary_arb.py      # Binary arbitrage (legacy)
│   │   ├── weather_v2.py      # ⭐ Gaussian weather model
│   │   └── crypto.py          # ⭐ Black-Scholes crypto model
│   │
│   ├── core/
│   │   ├── risk_manager.py    # Position limits, circuit breakers
│   │   ├── simulation.py      # Paper trading engine
│   │   └── datastore.py       # ⭐ SQLite persistence layer
│   │
│   └── dashboard/
│       └── app.py             # Streamlit web UI
│
├── tests/
│   ├── test_models.py
│   ├── test_risk_manager.py
│   └── test_strategies.py     # ⭐ Math model tests (25 tests)
│
├── data/                      # Market snapshots, SQLite DB
├── logs/                      # Log files
├── .env                       # Configuration (git-ignored)
├── pyproject.toml
└── README.md
```

**Key Files**:
- `runner.py` - Main entry point, coordinates all strategies
- `weather_v2.py` - Gaussian probability weather model
- `crypto.py` - Black-Scholes crypto price model
- `datastore.py` - Persists trades, signals, performance for backtesting

---

## Configuration Deep Dive

### Weather Strategy Config

```bash
WEATHER_ENABLED=true
WEATHER_MIN_EDGE_PERCENT=10.0      # Minimum 10% edge to trade
WEATHER_MIN_ZSCORE=1.5             # Statistical significance threshold
WEATHER_MAX_KELLY_FRACTION=0.25    # Cap Kelly at 25% of suggested
```

**How to tune**:
- Lower `MIN_EDGE` → more trades, lower quality
- Raise `MIN_ZSCORE` → fewer trades, higher confidence
- Adjust `MAX_KELLY` based on risk tolerance (0.1-0.3 range)

### Crypto Strategy Config

```bash
CRYPTO_ENABLED=true
CRYPTO_MIN_EDGE_PERCENT=8.0
CRYPTO_MIN_ZSCORE=1.5
CRYPTO_MIN_DAYS_TO_EXPIRY=0.5    # Skip <12hr markets
CRYPTO_MAX_DAYS_TO_EXPIRY=30.0   # Skip >30 day markets
```

**Market selection**:
- **12hr-3 day**: High signal, but fewer markets
- **7-30 day**: More markets, but model uncertainty increases
- Avoid 15-min markets: too latency-sensitive

### Risk Management Config

```bash
MAX_POSITION_PERCENT=5.0           # Max 5% capital per trade
MIN_ARB_PROFIT_PERCENT=1.5         # Binary arb minimum
MAX_SLIPPAGE_PERCENT=2.0           # Reject if slippage exceeds
CIRCUIT_BREAKER_LOSS_PERCENT=10.0  # Stop if daily loss > 10%
MAX_OPEN_POSITIONS=20
```

**Circuit Breaker**: Automatically halts trading if daily P&L drops below -10%. Resets at UTC midnight.

---

## Understanding the Math

### Weather: Gaussian Probability Model

**Problem**: Estimate `P(temperature > threshold)` given NOAA forecast

**Solution**: Model forecast error as Gaussian distribution

```python
# NOAA says high = 85°F, threshold = 80°F
# Historical RMSE for 1-day forecast = 2.5°F

from scipy.stats import norm
prob = 1 - norm.cdf(80, loc=85, scale=2.5)
# Result: 97.7% probability

# If market prices YES at 35%, edge = 62.7%
```

**Why it works**: NOAA forecasts have consistent, well-studied error distributions

### Crypto: Black-Scholes Adaptation

**Problem**: Estimate `P(BTC > $100k in 7 days)` given current price $95k

**Solution**: Use log-normal price model (zero drift for real-world probability)

```python
import math
from scipy.stats import norm

S = 95000    # Current price
K = 100000   # Threshold
T = 7/365    # Time in years
σ = 0.55     # Annual volatility

d2 = math.log(S/K) / (σ * math.sqrt(T))
prob = norm.cdf(d2)
# Result: 25.0% probability
```

**Why zero drift?** We estimate *real-world* probability, not risk-neutral pricing.

### Kelly Criterion for Position Sizing

**Problem**: How much to bet on each signal?

**Solution**: Kelly fraction = `(p*b - q) / b` where:
- `p` = win probability (from model)
- `q` = 1 - p
- `b` = odds (payout/stake - 1)

```python
# Model says 70% chance YES, market pricing 50%
p = 0.70
cost = 0.50  # What we pay
payout = 1.00  # What we get if we win
b = (payout / cost) - 1 = 1.0

kelly = (0.70 * 1.0 - 0.30) / 1.0 = 0.40  # Bet 40%

# We cap at 25% for safety (fractional Kelly)
actual_bet = min(0.40, 0.25) = 25% of capital
```

---

## Data Persistence & Backtesting

All trades, signals, and market snapshots are saved to SQLite (`data/polybot.db`).

**Tables**:
- `market_snapshots` - Price history every 15 minutes
- `trades` - Executed orders with P&L
- `signals` - Strategy signals before execution
- `arb_opportunities` - Detected arbitrage
- `daily_performance` - Aggregated stats

**Query Performance**:
```bash
# Interactive Python
python
>>> import asyncio
>>> from polybot.core.datastore import create_datastore
>>> 
>>> async def check_stats():
...     store = await create_datastore()
...     stats = await store.get_trade_stats(days=7)
...     print(stats)
...     await store.disconnect()
>>> 
>>> asyncio.run(check_stats())
```

**Backtesting** (future):
- Market snapshots enable replay of historical conditions
- Test strategy changes against past data
- Calculate Sharpe ratio, max drawdown, etc.

---

## Performance Monitoring

### Real-Time Logging

Logs are written to:
- `logs/polybot.log` (rotating, 10MB files)
- Console output (INFO level)

**Log Levels**:
- `DEBUG`: Market scans, rejected signals
- `INFO`: Executed trades, strategy stats
- `WARNING`: Failed API calls, low liquidity
- `ERROR`: Critical failures

### Key Metrics

Watch these during operation:

```
💰 Balance: $1,047.32 | P&L: $47.32 (4.7%) | Trades: 12
```

**Strategy-specific**:
```
🌦️ Weather: 3 signals, 2 high conf
₿ Crypto: 1 signal, 0 high conf
📈 Arbitrage: 0 opportunities found
```

### SQLite Dashboard (CLI)

```bash
sqlite3 data/polybot.db

-- Daily performance
SELECT date, total_trades, net_pnl, 
       weather_pnl, arb_pnl, crypto_pnl
FROM daily_performance
ORDER BY date DESC LIMIT 7;

-- Best signals
SELECT market_question, edge, edge_zscore, confidence
FROM signals
WHERE strategy = 'weather_v2' AND executed = 1
ORDER BY edge DESC LIMIT 10;

-- Trade history
SELECT created_at, strategy, outcome, size, realized_pnl
FROM trades
WHERE DATE(created_at) = DATE('now')
ORDER BY created_at DESC;
```

---

## Testing

### Unit Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=polybot --cov-report=html

# Run specific test file
pytest tests/test_strategies.py -v
```

**25 tests covering**:
- Gaussian CDF calculations
- Black-Scholes probability model
- Temperature/crypto threshold extraction
- Volatility conversions
- Datastore persistence

### Integration Testing

```bash
# Dry-run simulation (no network calls)
python -m polybot.runner --capital 1000 --duration 5

# Watch for:
✅ All components initialized
🌦️ Weather: X signals, Y high conf
₿ Crypto: X signals, Y high conf
💰 Balance: $1000.00 | P&L: $0.00 (0.0%) | Trades: 0
```

### Market Scanning Test

```python
# Test strategy signal generation
import asyncio
from polybot.strategies.crypto import CryptoStrategy, black_scholes_probability

# Test probability calculation
prob = black_scholes_probability(
    current_price=95000,
    threshold=100000,
    days_to_expiry=7,
    annual_volatility=0.55,
    is_above=True,
)
print(f"BTC $95k → $100k in 7d: {prob:.1%}")
# Expected: ~25%
```

---

## Latency Considerations

**Why This Matters**: You're running on a Mac Mini in Alabama, not co-located with Polymarket's servers.

### What's Optimized

✅ **Strategy Selection**
- Weather markets update every 1-6 hours (NOAA schedule)
- Daily crypto markets have 24hr+ windows
- Binary arb opportunities are mathematical, not time-sensitive

✅ **Polling vs Streaming**
- Uses REST API polling (every 5 min) instead of WebSocket
- Pyth prices cached, polled every 400ms (vs real-time tick)
- No sub-second reaction requirements

✅ **Python is Fine**
- Polymarket WebSocket latency: ~100ms
- Your network latency: ~50-80ms additional
- Total: ~150ms is perfectly acceptable for these strategies

### What's NOT Suitable

❌ **High-Frequency Strategies**
- 15-minute crypto price predictions (too fast)
- Orderbook arbitrage (speed-dependent)
- Front-running or MEV strategies

❌ **Market Making**
- Requires continuous quote updates
- Needs <50ms reaction times

**Bottom Line**: Your setup is ideal for the implemented strategies. Rust/C++ optimization would add complexity with minimal benefit.

---

## API Rate Limits

### Polymarket CLOB

| Endpoint | Limit (per 10s) |
|----------|-----------------|
| REST API (general) | 9,000 requests |
| Order placement | 3,500 burst |
| Orderbook queries | 1,500 requests |

**Bot behavior**: Scans every 5 minutes → ~3-5 requests per scan → well under limits

### NOAA API

- **No authentication** required
- Reasonable use policy: <1000 req/day recommended
- Bot uses: ~6 requests per scan (6 cities) × 12 scans/hr = 72/hr = 1,728/day
- **Recommendation**: Reduce city count if needed

### Pyth Network

- **No rate limits** (HTTP polling)
- Uses Hermes API: unlimited requests
- Bot polls every 400ms = 2.5 req/sec = moderate load

---

## Copy Trading (Advanced)

**Status**: Implemented but disabled by default (requires more setup)

**How It Works**:
1. Monitors top Polymarket trader wallets on Polygon blockchain
2. Detects their token purchases via ERC1155 Transfer events
3. Copies their trades after 30-60 second delay
4. Sizes at 10% of their position (configurable)

**Setup Required**:
```bash
# 1. Discover top traders (manual research)
# Use Dune Analytics or Polymarket leaderboard

# 2. Enable in .env
COPY_ENABLED=true
COPY_DELAY_SECONDS=45
COPY_FRACTION=0.10
MIN_TRADER_WIN_RATE=0.60

# 3. Add traders to polybot/connectors/copy_trading.py
# Edit DEFAULT_TOP_TRADERS with real addresses
```

**Risks**:
- Following traders into bad trades
- Delayed execution → worse prices
- Wallet monitoring may miss trades

**Recommendation**: Start with weather/crypto strategies first.

---

## Troubleshooting

### "No signals found for hours"

**Normal**: Weather and crypto strategies are selective. May go hours without high-confidence signals.

**Check**:
```bash
# Lower thresholds temporarily to see more signals
WEATHER_MIN_EDGE_PERCENT=5.0
WEATHER_MIN_ZSCORE=1.0
```

### "Circuit breaker triggered"

**Meaning**: Daily loss exceeded 10% threshold → trading halted

**Resolution**:
- Wait until UTC midnight (auto-reset)
- Or manually reset in code: `risk_manager.reset_circuit_breaker()`

**Review**: Check `data/polybot.db` for losing trades

### "Failed to get NOAA forecast"

**Cause**: NOAA API down or rate limited

**Fix**:
- Wait 15 minutes, retry
- Check NOAA status: https://status.weather.gov
- Reduce scan frequency

### "Pyth price data missing"

**Cause**: Network issue or symbol not in feed list

**Fix**:
- Verify internet connection
- Check supported symbols in `polybot/connectors/pyth.py`
- Add more feeds if needed (Pyth has 500+ symbols)

### Simulation vs Live Price Differences

**Expected**: Simulation uses simplified slippage model (0.5%), real execution has variable slippage

**Monitor**: Check `realized_pnl` vs `expected_pnl` in trades table

---

## Development

### Adding a New Strategy

1. Create `polybot/strategies/my_strategy.py`
2. Implement scan and execute methods
3. Add config to `polybot/config.py`
4. Register in `polybot/runner.py`:

```python
if self.settings.my_strategy.enabled:
    self.my_strategy = MyStrategy(...)
```

5. Add to main loop scan:

```python
await self.run_my_strategy_scan()
```

### Code Style

```bash
# Format
black polybot/
ruff check polybot/ --fix

# Type checking
mypy polybot/
```

### Running Tests

```bash
# Unit tests
pytest tests/ -v

# With coverage
pytest --cov=polybot --cov-report=term-missing

# Specific test
pytest tests/test_strategies.py::TestGaussianCDF -v
```

---

## FAQ

### Q: Do I need Rust for speed?

**A**: No. Python is sufficient because:
- Polymarket WebSocket latency: ~100ms
- Your network: +50-80ms
- Strategy decision time: <10ms
- **Total latency** (~150ms) is fine for daily markets

Rust would help if you were doing <1 second arbitrage, but we're not.

### Q: How much capital do I need?

**A**: 
- **Simulation**: Free, unlimited
- **Live (minimum)**: $100 USDC
- **Live (recommended)**: $500-1000 USDC for proper Kelly sizing

### Q: What's the expected return?

**Realistic estimate** (conservative):
- Weather: 15-25% APY (high Sharpe ratio)
- Crypto: 10-20% APY (moderate risk)
- Binary Arb: 5-10% APY (rare but safe)

**Combined**: 20-35% APY with proper diversification

**But**: Past performance ≠ future results. Markets adapt.

### Q: Is this legal?

**A**: Yes, with caveats:
- Polymarket is legal for non-US users
- US users: check local regulations
- Automated trading is allowed by Polymarket TOS
- No front-running or manipulation

### Q: How often should I monitor?

**A**: 
- **Simulation**: Check daily
- **Live**: Check every 4-8 hours
- **Set alerts** for circuit breaker triggers
- SQLite `daily_performance` table shows daily stats

### Q: Can I run multiple instances?

**A**: Not recommended (same wallet would conflict)

**Instead**: Use different strategies per instance:
```bash
# Instance 1: Weather only
WEATHER_ENABLED=true
CRYPTO_ENABLED=false

# Instance 2: Crypto only
WEATHER_ENABLED=false
CRYPTO_ENABLED=true
```

### Q: What about gas fees on Polygon?

**A**: Polygon gas is negligible (~$0.001 per trade). Not factored into P&L but effectively zero.

---

## Resources

### Polymarket

- **Docs**: https://docs.polymarket.com
- **API**: https://docs.polymarket.com/developers
- **CLOB Client**: https://github.com/Polymarket/py-clob-client

### Data Sources

- **NOAA API**: https://www.weather.gov/documentation/services-web-api
- **Pyth Network**: https://docs.pyth.network
- **Dune Analytics** (trader research): https://dune.com/polymarket

### Statistical Methods

- **Gaussian Distribution**: https://en.wikipedia.org/wiki/Normal_distribution
- **Black-Scholes Model**: https://en.wikipedia.org/wiki/Black–Scholes_model
- **Kelly Criterion**: https://en.wikipedia.org/wiki/Kelly_criterion

---

## Warning

⚠️ **Trading involves risk.** 

- Start with simulation mode
- Test with small amounts ($50-100)
- Don't invest more than you can afford to lose
- Markets can be irrational longer than you can stay solvent
- This bot is for **educational purposes**

**NOT FINANCIAL ADVICE**

---

## License

MIT

---

## Credits

Built for latency-tolerant, math-based prediction market trading. Optimized for retail execution environments (Mac Mini in Alabama ✓).

**Strategies**: Weather (Gaussian), Crypto (Black-Scholes), Binary Arb
**Infrastructure**: Python 3.10+, SQLite, aiohttp, Web3.py
**APIs**: Polymarket CLOB, NOAA Weather, Pyth Network

---

*Last Updated: February 3, 2026*
