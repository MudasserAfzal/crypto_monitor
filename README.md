# CryptoSignal

Daily, actionable cryptocurrency **buy / sell / short** signals powered by multi-source market data, a full technical-analysis suite, sentiment & on-chain feeds, and a weighted multi-factor scoring engine.

> **Risk disclaimer:** This software is for educational and informational purposes only. It is **not financial advice**. Cryptocurrency trading carries substantial risk of loss. Always do your own research.

---

## Local storage (no database)

All persistence lives under `data/`:

| Path | Contents |
|------|----------|
| `data/tickers/all_tickers.json` | Full coin universe (CoinGecko pages + Binance USDT) |
| `data/tickers/by_symbol.json` | Fast symbol lookup |
| `data/universe/symbols.json` | Symbol list |
| `data/ohlcv/{SYM}_{TF}.json` | Candles per asset/timeframe |
| `data/signals/all_signals.json` | Last signal run |
| `data/reports/latest.json` | Latest daily report |
| `data/sentiment/`, `data/overview/`, `data/onchain/` | Supporting snapshots |

```bash
# Extract every coin (≈1000 from CoinGecko + all Binance USDT pairs)
python -m app.cli --extract-only

# Extract all coins, then score top 50 by volume
python -m app.cli

# Score more of the universe
python -m app.cli --signal-limit 100
```

Retrieve without re-fetching: `GET /api/v1/market/stored`, `GET /api/v1/signals/report` (cached), `GET /api/v1/market/symbols`.

### Quick start

```bash
cp .env.example .env
cd backend && source .venv/bin/activate
pip install -r requirements.txt   # first time
PYTHONPATH=. python -m app.cli --extract-only
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
# another terminal:
cd frontend && npm run dev
```

---

## Architecture

| Layer | Stack |
|-------|--------|
| API | FastAPI + Uvicorn |
| Persistence | Local JSON under `data/` (no DB required) |
| Optional cache | Redis |
| Optional jobs | Celery Beat |
| TA / ML | NumPy / Pandas / scikit-learn / XGBoost |
| UI | React + Vite + Lightweight Charts + Zustand |

```
backend/app/
  collectors/   # Multi-API ingestion + fallback aggregator
  indicators/   # Full TA library + multi-timeframe engine
  signals/      # Weighted scoring, confidence, risk sizing
  ml/           # Random Forest / XGBoost / MLP sequence model
  backtesting/  # Walk-forward simulation + Sharpe / DD
  api/          # REST endpoints
  workers/      # Celery scheduled jobs
  services/     # Telegram + email alerts
frontend/       # Live dashboard
```

---

## Signal model

| Factor | Default weight |
|--------|----------------|
| Technical indicators | 40% |
| Sentiment | 20% |
| On-chain | 20% |
| Volume / order flow | 10% |
| Macro / news | 10% |

Weights are configurable via `.env` (`WEIGHT_*`).

Each run emits:

- Primary: `BUY` / `SELL` / `HOLD` + confidence %
- Secondary: `LONG` / `SHORT` / `NONE` + leverage suggestion
- Entry, take-profit, stop-loss, risk/reward, portfolio allocation %
- Time horizon: short / medium / long

---

## Data sources

**Always-on (no key):** CoinGecko, Binance public, Kraken public, KuCoin public, Fear & Greed, DefiLlama, Reddit public JSON, CryptoPanic public, Yahoo Finance.

**Key-activated:** CoinMarketCap, LunarCrush, Glassnode, Covalent, Dune, Etherscan, Blockchair, NewsAPI, Finnhub, FRED, GitHub, Telegram, SMTP.

Collectors use rate limiting, exponential backoff, Redis caching, and automatic fallback (e.g. OHLCV: Binance → KuCoin → Kraken → CoinGecko).

---

## Indicators implemented

Trend: SMA (5–200), EMA (9–200), WMA, HMA, MACD, Ichimoku, Parabolic SAR, ADX  

Momentum: RSI (7/14/21), Stochastic, CCI, MFI, OBV, ROC, Williams %R, Ultimate Oscillator, Trix  

Volatility: Bollinger (1/2/3σ), ATR, Keltner, Donchian, StdDev / z-score  

Volume: Volume MA, CMF, A/D Line, VPT, VWAP  

Patterns / levels: Fibonacci, Pivot Points (Standard / Fib / Woodie / Camarilla / Demark), MA envelopes, trendlines, H&S / triangles / flags / wedges / cup & handle, Heikin-Ashi, Renko hint  

Multi-timeframe analysis on `1h`, `4h`, `1d`, `1w` (configurable).

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/market/overview` | Global + trending + F&G |
| GET | `/api/v1/market/tickers` | Latest prices |
| GET | `/api/v1/market/ohlcv/{symbol}` | Candles |
| GET | `/api/v1/sentiment` | Sentiment bundle |
| GET | `/api/v1/signals/analyze/{symbol}` | Single-asset signal |
| GET | `/api/v1/signals/report` | Full daily report |
| POST | `/api/v1/signals/run` | Same, POST body symbols |
| POST | `/api/v1/backtest/{symbol}` | Historical simulation |

---

## Tests

```bash
cd backend
pytest -q
```

---

## Production notes

- Never commit `.env` — keys live in environment variables only
- Schedule via Celery Beat or cron: `python -m app.cli --notify`
- Prometheus/Grafana can scrape `/api/v1/health`; extend as needed
- Past performance and backtests do **not** guarantee future results
# crypto_monitor
