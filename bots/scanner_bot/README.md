# MTB Scanner API v2.0

Production-ready FastAPI service that exposes the CryptoScanner_MTB trading-signal engine over HTTP. Deployable to Railway in one click — no Telegram, no ngrok, no Google Drive required.

---

## Features

- **12 REST endpoints** covering signals, market state, performance stats, watchlist CRUD, and runtime telemetry
- **Background scanner loop** — fetches live ticker data and re-scores every coin on a configurable interval
- **Startup self-test** — verifies storage, scanner instance, background loop, and all routes at boot
- **Global exception handlers** — every endpoint returns structured JSON even on unexpected errors; service never crashes
- **Hourly atomic backups** — all three data files copied to `data/backups/` via `.tmp` + `os.replace()` (crash-safe)
- **Graceful shutdown** — SIGTERM saves all data files and cancels background tasks before exit
- **Railway-native** — binds `0.0.0.0:$PORT`, Nixpacks build, `Procfile` + `railway.json` included

---

## Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11 |
| Framework | FastAPI 0.111+ |
| Server | Uvicorn 0.29+ (ASGI) |
| HTTP client | Requests 2.31+ |
| Scanner engine | `scanner.py` (CryptoScanner_MTB v1, unchanged) |

---

## Quick Start (local)

```bash
cd scanner_api
pip install -r requirements.txt
PORT=8000 python main.py
```

The server starts on `http://localhost:8000`. The startup self-test output appears in the console:

```
🚀 Scanner V2 Started
   Storage Ready           ✅
   Scanner Ready           ✅
   Background Loop Ready   ✅
   API Ready               ✅
```

---

## Deploy to Railway

### Option A — Root directory deploy

1. Push this `scanner_api/` folder as a standalone GitHub repository.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Railway auto-detects the `Procfile` and installs `requirements.txt` via Nixpacks.
4. `PORT` is set automatically by Railway.

### Option B — Monorepo deploy

1. In the Railway service settings set **Root Directory** to `scanner_api`.
2. Deploy — Railway uses the `Procfile` inside that directory.

### Option C — railway.json

The included `railway.json` configures Nixpacks build + ON_FAILURE restart policy (max 10 retries). No additional configuration needed.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | HTTP port (Railway sets this automatically) |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scanner cycles (5 min) |
| `BACKUP_INTERVAL_SECONDS` | `3600` | Seconds between automatic backups (1 hour) |
| `DISCOVERY_MAX_COINS` | `500` | Max coins evaluated per discovery pass |
| `MIN_VOLUME_24H` | `500000` | 24h volume filter for discovery coins |
| `BOOTSTRAP_ENABLED` | `true` | Pre-load 120 ticks of history on startup |

---

## API Reference

### Health

#### `GET /health`
Liveness probe. Returns immediately without touching scanner state.

```json
{
  "status": "healthy",
  "service": "scanner_v2",
  "version": "2.0"
}
```

---

### Signals

#### `GET /api/v1/scanner/signals?strategy=MTB`
Latest MTB-qualified signals (Elite / High / Medium priority), newest first.

Query params:
- `strategy` — must be `MTB` (default). Any other value returns `[]`.

```json
[
  {
    "coin": "BTC",
    "market_state": "breakout",
    "opportunity_type": "momentum_trade",
    "priority": "Elite",
    "score": 92,
    "confidence": 88,
    "risk": "low",
    "timestamp": "2025-01-15T10:30:00+00:00"
  }
]
```

---

### Market State

#### `GET /api/v1/scanner/market-state`
Aggregate market state across all tracked coins.

```json
{
  "market_state": "breakout",
  "timestamp": "2025-01-15T10:30:00+00:00"
}
```

Possible values: `breakout` · `bull_trend` · `recovery` · `pullback` · `sideways` · `downtrend`

---

### Performance

#### `GET /api/v1/scanner/performance`
Win-rate and return statistics computed from the signal tracker log.

```json
{
  "status": "success",
  "model": "v12.2",
  "signals_total": 120,
  "signals_evaluated": 45,
  "win_rate": 67.5,
  "avg_returns": {"1h": 0.42, "4h": 1.15, "24h": 2.87},
  "best_coin": "SOL",
  "best_coin_return_24h": 8.3,
  "market_state_distribution": {
    "breakout": 80, "bull_trend": 15, "recovery": 20,
    "pullback": 3, "sideways": 2, "downtrend": 0
  },
  "priority_distribution": {
    "Elite": 30, "High": 45, "Medium": 35, "Watch": 8, "Ignore": 2
  },
  "timestamp": "2025-01-15T10:30:00+00:00"
}
```

---

### Recent Signals

#### `GET /api/v1/scanner/recent?limit=10`
The N most-recently logged signals from the tracker (newest first).

Query params:
- `limit` — integer 1–200, default 10.

```json
[
  {
    "coin": "ETH",
    "market_state": "breakout",
    "opportunity_type": "momentum_trade",
    "priority": "High",
    "score": 81,
    "confidence": 78,
    "timestamp": "2025-01-15T10:25:00+00:00"
  }
]
```

---

### Storage

#### `GET /api/v1/scanner/storage`
Filesystem status of the scanner's data directory.

```json
{
  "signals_file_exists": true,
  "stats_file_exists": true,
  "history_file_exists": true,
  "signals_count": 120,
  "storage_path": "/app/scanner_api/data",
  "last_updated": "2025-01-15T10:28:00+00:00"
}
```

---

### Coins

#### `GET /api/v1/scanner/coins`
Per-coin in-memory history depth and signal-readiness flags, sorted by history length descending.

```json
[
  {
    "coin": "BTC",
    "history_len": 120,
    "ema_ready": true,
    "mtf_ready": true,
    "phase5_ready": true,
    "market_state": "breakout"
  }
]
```

---

### Watchlist

#### `GET /api/v1/scanner/watchlist`
```json
{"count": 6, "coins": ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA"]}
```

#### `POST /api/v1/scanner/watchlist`
Body: `{"coin": "BTC"}`

```json
{"status": "success", "coin": "BTC", "count": 7}
```

#### `DELETE /api/v1/scanner/watchlist/{coin}`
Idempotent — succeeds even if the coin is not on the list.

```json
{"status": "success", "removed": "BTC", "count": 6}
```

---

### Status

#### `GET /api/v1/scanner/status`
Runtime telemetry — safe to poll frequently (reads only in-memory state).

```json
{
  "service": "scanner_v2",
  "version": "2.0",
  "running": true,
  "uptime_seconds": 3600,
  "last_scan_time": "2025-01-15T10:30:00+00:00",
  "scan_cycles": 12,
  "signals_generated": 180,
  "watchlist_size": 6,
  "memory_signals": 15,
  "storage_ready": true,
  "railway": true
}
```

---

### Metrics

#### `GET /api/v1/scanner/metrics`
Aggregated counts from the signal tracker.

```json
{
  "signals_total": 120,
  "elite": 30,
  "high": 45,
  "medium": 35,
  "market_states": {
    "breakout": 80, "bull_trend": 15, "recovery": 20,
    "pullback": 3, "sideways": 2, "downtrend": 0
  },
  "avg_returns": {"1h": 0.42, "4h": 1.15, "24h": 2.87},
  "win_rate": 67.5
}
```

---

## Architecture

```
scanner_api/
├── main.py          FastAPI app, all 12 endpoints, startup/shutdown lifecycle
│   ├── _scanner_loop()      asyncio background task (runs forever)
│   │   ├── Scanner.run_bootstrap()   pre-loads 120 ticks of price history
│   │   └── Scanner.scan_*()          every SCAN_INTERVAL_SECONDS seconds
│   ├── _backup_loop()       asyncio background task (hourly)
│   └── _do_shutdown_save()  SIGTERM handler — saves data + cancels tasks
├── scanner.py       CryptoScanner_MTB v1 engine (unchanged from notebook)
├── requirements.txt
├── Procfile         web: python main.py
├── railway.json     Nixpacks build + ON_FAILURE restart policy
└── data/            runtime data (gitignored)
    ├── signals.json
    ├── stats.json
    ├── watchlist.json
    └── backups/
        ├── signals_backup.json
        ├── stats_backup.json
        └── watchlist_backup.json
```

### Signal flow

```
Binance API
    └─ Scanner.get_tickers()
         └─ Scanner.scan_watchlist() + scan_market()
              └─ smart_filter() → learning_filter() → historical_filter()
                   └─ LATEST_MTB_SIGNALS  (module-level list)
                        └─ GET /api/v1/scanner/signals  ← client reads here
```

---

## Bug Fixed vs Notebook

The notebook crashed on every scanner cycle:

```
AttributeError: 'Signal' object has no attribute 'exch_perf_90d'
```

Fix: added `exch_perf_90d: Optional[float] = None` to the `Signal` dataclass
and populated it from `get_historical_performance()` inside `analyze_coin()`.
All other scanner logic is unchanged.

---

## License

MIT
