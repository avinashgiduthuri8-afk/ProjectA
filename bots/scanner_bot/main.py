"""
MTB Scanner API v2.0 — Railway-compatible FastAPI service.

Wraps CryptoScanner_MTB scanner v1 logic and exposes it over HTTP.
The scanner runs as an asyncio background task; all endpoints are read-only
projections of in-memory state — zero blocking I/O on the request path.

Endpoints
─────────
  GET    /health                               Liveness probe
  GET    /api/v1/scanner/signals?strategy=MTB  Latest MTB signals (list, newest first)
  GET    /api/v1/scanner/market-state          Aggregate market state across all coins
  GET    /api/v1/scanner/performance           Win-rate / return stats from tracker
  GET    /api/v1/scanner/recent?limit=N        N most-recently logged signals
  GET    /api/v1/scanner/storage               Data-directory filesystem status
  GET    /api/v1/scanner/coins                 Per-coin history depth + readiness flags
  GET    /api/v1/scanner/watchlist             Current watchlist
  POST   /api/v1/scanner/watchlist             Add a coin  {"coin": "BTC"}
  DELETE /api/v1/scanner/watchlist/{coin}      Remove a coin
  GET    /api/v1/scanner/status                Runtime telemetry snapshot
  GET    /api/v1/scanner/metrics               Aggregated signal counts + win-rate

Production features
───────────────────
  • Startup self-test  — storage, scanner, loop, and routes verified at boot
  • Global exception handlers — StarletteHTTPException / RequestValidationError / Exception
  • Hourly atomic backups  → data/backups/{signals,stats,watchlist}_backup.json
  • Graceful shutdown      — saves all files + cancels background tasks on SIGTERM

Deployment
──────────
  Binds to 0.0.0.0:PORT (Railway sets PORT automatically).
  No ngrok, no localhost hard-coding, no Telegram, no Google Drive.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .scanner import (
    Scanner,
    Signal,
    SignalPerformanceTracker,
    WatchlistStore,
    detect_market_state,
    smart_filter,
    learning_filter,
    historical_filter,
    # storage paths & readiness thresholds (read-only, no logic change)
    STORAGE_DIR,
    SIGNAL_LOG_FILE,
    STATS_FILE,
    EMA_SLOW_PERIOD,
    MTF_1H_WINDOW,
    _READY_P5,
)

logger = logging.getLogger("scanner_api")

# =============================================================================
# SHARED STATE  (written by scanner background task, read by endpoints)
# =============================================================================

LATEST_MTB_SIGNALS: list[dict] = []
LATEST_MARKET_STATE: dict = {
    "market_state": "unknown",
    "timestamp":    datetime.now(timezone.utc).isoformat(),
}

# Promoted to module-level so /performance and /recent can read it.
# Set to a real instance by _scanner_loop() at startup; endpoints guard
# against None so they never crash before the loop has initialised.
_TRACKER: Optional[SignalPerformanceTracker] = None

# Exposed for /coins — reads scanner.price_history in-memory only.
_SCANNER: Optional[Scanner] = None

# Runtime telemetry — written by _scanner_loop(), read by /status
_SERVICE_START:      datetime       = datetime.now(timezone.utc)
_LAST_SCAN_TIME:     Optional[str]  = None
_SCAN_CYCLES:        int            = 0
_SIGNALS_GENERATED:  int            = 0

# Persistence helpers  [P2-SCN-V2.7C/D]
BACKUP_DIR = os.path.join(STORAGE_DIR, "backups")
_SCANNER_TASK: Optional[asyncio.Task] = None   # scanner background loop
_BACKUP_TASK:  Optional[asyncio.Task] = None   # hourly backup loop

# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(title="MTB Scanner API", version="2.0")


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status":  "healthy",
        "service": "scanner_v2",
        "version": "2.0",
    }


# ---------------------------------------------------------------------------
# 2. MTB Signals endpoint
# ---------------------------------------------------------------------------

_MTB_PRIORITIES = {"Elite", "High", "Medium"}

@app.get("/api/v1/scanner/signals")
async def scanner_signals(strategy: str = Query(default="MTB")):
    """
    Returns MTB-ready signals filtered to Elite / High / Medium priority only.
    Reads from tracker recent signals (same source as /recent).
    Newest first.
    """

    if strategy.upper() != "MTB":
        return JSONResponse(content=[])

    try:
        tracker = _TRACKER

        if tracker is None:
            return JSONResponse(content=[])

        recent = tracker.recent_signals(limit=100)

        filtered = [
            {
                "coin":             s.get("coin", ""),
                "market_state":     s.get("market_state", ""),
                "opportunity_type": s.get("opportunity_type", ""),
                "priority":         s.get("priority", ""),
                "score":            s.get("opportunity_score", 0),
                "confidence":       s.get("opp_confidence", 0),
                "risk":             s.get("risk_level", ""),
                "timestamp":        s.get("timestamp", ""),
            }
            for s in recent
            if s.get("priority") in _MTB_PRIORITIES
        ]

        filtered.sort(
            key=lambda x: x["timestamp"],
            reverse=True
        )

        return JSONResponse(content=filtered)

    except Exception:
        logger.exception("/signals: unexpected error")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 3. Market State endpoint
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/market-state")
async def market_state():
    """
    Returns the current aggregate market state across all tracked coins.
    Always returns a dict — never None.
    """
    return JSONResponse(content=LATEST_MARKET_STATE)


# ---------------------------------------------------------------------------
# 4. Performance endpoint  [P2-SCN-V2.2]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/performance")
async def scanner_performance():
    """
    Returns win-rate and return statistics computed from the signal log.
    Reads from the SignalPerformanceTracker only — no live scanning.
    Always returns HTTP 200 with safe defaults on empty / uninitialized data.
    """
    _SAFE: dict = {
        "status":      "success",
        "model":       "v12.2",
        "signals_total":     0,
        "signals_evaluated": 0,
        "win_rate":          0.0,
        "avg_returns":       {"1h": None, "4h": None, "24h": None},
        "best_coin":              None,
        "best_coin_return_24h":   None,
        "market_state_distribution": {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        },
        "priority_distribution": {
            "Elite": 0, "High": 0, "Medium": 0, "Watch": 0, "Ignore": 0,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=_SAFE)

        raw_signals: list[dict] = tracker._data.get("signals", [])

        # ── totals ──────────────────────────────────────────────────────────
        signals_total = len(raw_signals)

        # ── evaluation buckets ──────────────────────────────────────────────
        horizons = ("1h", "4h", "24h")
        returns_by_horizon: dict[str, list[float]] = {h: [] for h in horizons}
        evaluated_set: set[int] = set()     # indices of signals with ≥1 eval

        best_coin: Optional[str]        = None
        best_return_24h: Optional[float] = None

        for idx, item in enumerate(raw_signals):
            evals: dict = item.get("evaluations") or {}
            for h in horizons:
                ev = evals.get(h)
                if ev:
                    try:
                        pct = float(ev["change_percent"])
                        returns_by_horizon[h].append(pct)
                        evaluated_set.add(idx)
                    except (KeyError, TypeError, ValueError):
                        pass
            # best_coin by 24h return
            ev24 = evals.get("24h")
            if ev24:
                try:
                    r24 = float(ev24["change_percent"])
                    if best_return_24h is None or r24 > best_return_24h:
                        best_return_24h = r24
                        best_coin = item.get("coin")
                except (KeyError, TypeError, ValueError):
                    pass

        signals_evaluated = len(evaluated_set)

        # win_rate = % of evaluated signals where latest return > 0
        wins = 0
        for idx in evaluated_set:
            item  = raw_signals[idx]
            evals = item.get("evaluations") or {}
            for h in ("24h", "4h", "1h"):
                ev = evals.get(h)
                if ev:
                    try:
                        if float(ev["change_percent"]) > 0:
                            wins += 1
                    except (KeyError, TypeError, ValueError):
                        pass
                    break   # use the longest available horizon for win/loss

        win_rate = round(wins / signals_evaluated * 100, 2) if signals_evaluated else 0.0

        def _safe_avg(vals: list[float]) -> Optional[float]:
            if not vals:
                return None
            return round(sum(vals) / len(vals), 4)

        avg_returns = {h: _safe_avg(returns_by_horizon[h]) for h in horizons}

        # ── distributions ────────────────────────────────────────────────────
        ms_dist: dict[str, int] = {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        }
        pri_dist: dict[str, int] = {
            "Elite": 0, "High": 0, "Medium": 0, "Watch": 0, "Ignore": 0,
        }

        for item in raw_signals:
            ms  = item.get("market_state", "")
            pri = item.get("priority",     "")
            if ms  in ms_dist:  ms_dist[ms]   += 1
            if pri in pri_dist: pri_dist[pri] += 1

        return JSONResponse(content={
            "status":      "success",
            "model":       "v12.2",
            "signals_total":     signals_total,
            "signals_evaluated": signals_evaluated,
            "win_rate":          win_rate,
            "avg_returns":       avg_returns,
            "best_coin":              best_coin,
            "best_coin_return_24h":   best_return_24h,
            "market_state_distribution": ms_dist,
            "priority_distribution":     pri_dist,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    except Exception:
        logger.exception("/performance: unexpected error — returning safe defaults")
        _SAFE["timestamp"] = datetime.now(timezone.utc).isoformat()
        return JSONResponse(content=_SAFE)


# ---------------------------------------------------------------------------
# 5. Recent signals endpoint  [P2-SCN-V2.2]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/recent")
async def scanner_recent(limit: int = Query(default=10, ge=1, le=200)):
    """
    Returns the most recently logged signals from the performance tracker.
    Newest first.  Reads stored data — no live scanning.
    Always returns a list; returns [] on empty / uninitialized data.
    """
    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=[])

        recent = tracker.recent_signals(limit=limit)
        result = []
        for item in recent:
            result.append({
                "coin":             item.get("coin",             ""),
                "market_state":     item.get("market_state",     ""),
                "opportunity_type": item.get("opportunity_type", ""),
                "priority":         item.get("priority",         ""),
                "score":            item.get("opportunity_score", 0),
                "confidence":       item.get("opp_confidence",    0),
                "timestamp":        item.get("timestamp",         ""),
            })
        return JSONResponse(content=result)

    except Exception:
        logger.exception("/recent: unexpected error — returning []")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 6. Storage endpoint  [P2-SCN-V2.3]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/storage")
async def scanner_storage():
    """
    Returns filesystem status for the scanner's data directory.
    Reads existing files only — no scanning, no HTTP calls.
    Always returns HTTP 200; missing files are reported as False / 0 / null.
    """
    try:
        from pathlib import Path

        signals_path = Path(SIGNAL_LOG_FILE)
        stats_path   = Path(STATS_FILE)
        # price history lives in-memory; we report whether the data dir exists
        history_path = Path(STORAGE_DIR)

        signals_exists = signals_path.is_file()
        stats_exists   = stats_path.is_file()
        history_exists = history_path.is_dir()

        # signals count from tracker if available, else parse file directly
        signals_count = 0
        tracker = _TRACKER
        if tracker is not None:
            signals_count = len(tracker._data.get("signals", []))
        elif signals_exists:
            try:
                import json as _json
                data = _json.loads(signals_path.read_text(encoding="utf-8"))
                signals_count = len(data.get("signals", []))
            except Exception:
                signals_count = 0

        # last_updated = mtime of signals file
        last_updated: Optional[str] = None
        if signals_exists:
            try:
                mtime = signals_path.stat().st_mtime
                last_updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

        return JSONResponse(content={
            "signals_file_exists": signals_exists,
            "stats_file_exists":   stats_exists,
            "history_file_exists": history_exists,
            "signals_count":       signals_count,
            "storage_path":        str(STORAGE_DIR),
            "last_updated":        last_updated,
        })

    except Exception:
        logger.exception("/storage: unexpected error — returning safe defaults")
        return JSONResponse(content={
            "signals_file_exists": False,
            "stats_file_exists":   False,
            "history_file_exists": False,
            "signals_count":       0,
            "storage_path":        str(STORAGE_DIR),
            "last_updated":        None,
        })


# ---------------------------------------------------------------------------
# 7. Coins endpoint  [P2-SCN-V2.3]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/coins")
async def scanner_coins():
    """
    Returns per-coin history depth and readiness flags from the scanner's
    in-memory price_history dict.  No live rescanning, no HTTP calls.
    Always returns a list; returns [] when the scanner is not yet running.
    """
    try:
        sc = _SCANNER
        if sc is None:
            return JSONResponse(content=[])

        result = []
        for coin, history in sc.price_history.items():
            n = len(history)
            ema_ready    = n >= EMA_SLOW_PERIOD   # 21 ticks
            mtf_ready    = n >= MTF_1H_WINDOW     # 48 ticks
            phase5_ready = n >= _READY_P5         # 20 ticks

            try:
                ms: Optional[str] = detect_market_state(history) if n >= 6 else None
            except Exception:
                ms = None

            result.append({
                "coin":         coin,
                "history_len":  n,
                "ema_ready":    ema_ready,
                "mtf_ready":    mtf_ready,
                "phase5_ready": phase5_ready,
                "market_state": ms,
            })

        # sort: longest history first so callers see the most data-rich coins up top
        result.sort(key=lambda x: x["history_len"], reverse=True)
        return JSONResponse(content=result)

    except Exception:
        logger.exception("/coins: unexpected error — returning []")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 8. Watchlist GET  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/watchlist")
async def watchlist_get():
    """Return the current watchlist. HTTP 200 always."""
    try:
        sc = _SCANNER
        if sc is None:
            # scanner not yet started — load directly from file
            store = WatchlistStore()
            coins = store.all()
        else:
            coins = sc.watchlist_store.all()
        return JSONResponse(content={"count": len(coins), "coins": coins})
    except Exception:
        logger.exception("/watchlist GET: unexpected error")
        return JSONResponse(content={"count": 0, "coins": []})


# ---------------------------------------------------------------------------
# 9. Watchlist POST  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

class _AddCoinBody(BaseModel):
    coin: str


@app.post("/api/v1/scanner/watchlist")
async def watchlist_add(body: _AddCoinBody):
    """
    Add a coin to the watchlist.
    Duplicates are silently ignored.  Coin symbol is uppercased.
    HTTP 200 always.
    """
    try:
        symbol = body.coin.strip().upper()
        sc = _SCANNER
        store = sc.watchlist_store if sc is not None else WatchlistStore()
        store.add(symbol)          # no-op + no error if duplicate
        coins = store.all()
        return JSONResponse(content={
            "status": "success",
            "coin":   symbol,
            "count":  len(coins),
        })
    except Exception:
        logger.exception("/watchlist POST: unexpected error")
        return JSONResponse(content={"status": "error", "coin": "", "count": 0})


# ---------------------------------------------------------------------------
# 10. Watchlist DELETE  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

@app.delete("/api/v1/scanner/watchlist/{coin}")
async def watchlist_remove(
    coin: str = Path(..., description="Coin symbol to remove, e.g. BTC"),
):
    """
    Remove a coin from the watchlist.
    If the coin is not on the list the call still returns success — idempotent.
    HTTP 200 always.
    """
    try:
        symbol = coin.strip().upper()
        sc = _SCANNER
        store = sc.watchlist_store if sc is not None else WatchlistStore()
        store.remove(symbol)       # no-op + no error if not found
        coins = store.all()
        return JSONResponse(content={
            "status":  "success",
            "removed": symbol,
            "count":   len(coins),
        })
    except Exception:
        logger.exception("/watchlist DELETE: unexpected error")
        return JSONResponse(content={"status": "error", "removed": "", "count": 0})


# ---------------------------------------------------------------------------
# 11. Status endpoint  [P2-SCN-V2.6]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/status")
async def scanner_status():
    """
    Runtime health snapshot — reads in-memory state only, no scanning.
    HTTP 200 always.
    """
    try:
        from pathlib import Path as _Path
        uptime = int((datetime.now(timezone.utc) - _SERVICE_START).total_seconds())
        sc      = _SCANNER
        running = sc is not None

        watchlist_size = 0
        if sc is not None:
            try:
                watchlist_size = len(sc.watchlist_store.all())
            except Exception:
                pass

        storage_ready = _Path(STORAGE_DIR).is_dir()

        return JSONResponse(content={
            "service":           "scanner_v2",
            "version":           "2.0",
            "running":           running,
            "uptime_seconds":    uptime,
            "last_scan_time":    _LAST_SCAN_TIME,
            "scan_cycles":       _SCAN_CYCLES,
            "signals_generated": _SIGNALS_GENERATED,
            "watchlist_size":    watchlist_size,
            "memory_signals":    len(LATEST_MTB_SIGNALS),
            "storage_ready":     storage_ready,
            "railway":           True,
        })
    except Exception:
        logger.exception("/status: unexpected error — returning safe defaults")
        return JSONResponse(content={
            "service":           "scanner_v2",
            "version":           "2.0",
            "running":           False,
            "uptime_seconds":    0,
            "last_scan_time":    None,
            "scan_cycles":       0,
            "signals_generated": 0,
            "watchlist_size":    0,
            "memory_signals":    0,
            "storage_ready":     False,
            "railway":           True,
        })


# ---------------------------------------------------------------------------
# 12. Metrics endpoint  [P2-SCN-V2.6]
# ---------------------------------------------------------------------------

@app.get("/api/v1/scanner/metrics")
async def scanner_metrics():
    """
    Aggregated signal metrics from the tracker — no calculations beyond
    what the tracker already holds.  HTTP 200 always.
    """
    _SAFE = {
        "signals_total": 0,
        "elite":  0, "high": 0, "medium": 0,
        "market_states": {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        },
        "avg_returns": {"1h": None, "4h": None, "24h": None},
        "win_rate": 0.0,
    }

    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=_SAFE)

        raw: list[dict] = tracker._data.get("signals", [])

        # priority counts
        pri_counts = {"Elite": 0, "High": 0, "Medium": 0}
        ms_counts  = {k: 0 for k in _SAFE["market_states"]}

        horizons = ("1h", "4h", "24h")
        ret_buckets: dict[str, list[float]] = {h: [] for h in horizons}
        wins = 0; evaluated = 0

        for item in raw:
            pri = item.get("priority", "")
            if pri in pri_counts:
                pri_counts[pri] += 1

            ms = item.get("market_state", "")
            if ms in ms_counts:
                ms_counts[ms] += 1

            evals = item.get("evaluations") or {}
            has_eval = False
            for h in horizons:
                ev = evals.get(h)
                if ev:
                    try:
                        pct = float(ev["change_percent"])
                        ret_buckets[h].append(pct)
                        has_eval = True
                    except (KeyError, TypeError, ValueError):
                        pass
            if has_eval:
                evaluated += 1
                # win = positive return on the longest evaluated horizon
                for h in ("24h", "4h", "1h"):
                    ev = evals.get(h)
                    if ev:
                        try:
                            if float(ev["change_percent"]) > 0:
                                wins += 1
                        except (KeyError, TypeError, ValueError):
                            pass
                        break

        def _avg(vals: list) -> Optional[float]:
            return round(sum(vals) / len(vals), 4) if vals else None

        win_rate = round(wins / evaluated * 100, 2) if evaluated else 0.0

        return JSONResponse(content={
            "signals_total": len(raw),
            "elite":  pri_counts["Elite"],
            "high":   pri_counts["High"],
            "medium": pri_counts["Medium"],
            "market_states": ms_counts,
            "avg_returns":   {h: _avg(ret_buckets[h]) for h in horizons},
            "win_rate":      win_rate,
        })

    except Exception:
        logger.exception("/metrics: unexpected error — returning safe defaults")
        return JSONResponse(content=_SAFE)


# =============================================================================
# SCANNER BACKGROUND TASK
# =============================================================================

async def _no_op_alert(signal: Signal, source: str) -> None:
    """No-op alert callback — signals are served via the HTTP API instead."""
    pass


async def _scanner_loop() -> None:
    """
    Runs the Scanner indefinitely, refreshing LATEST_MTB_SIGNALS and
    LATEST_MARKET_STATE after every scan cycle.
    """
    global LATEST_MTB_SIGNALS, LATEST_MARKET_STATE, _TRACKER, _SCANNER, \
           _LAST_SCAN_TIME, _SCAN_CYCLES, _SIGNALS_GENERATED
    logger.info("ENTERED _scanner_loop")
    watchlist  = WatchlistStore()
    tracker    = SignalPerformanceTracker()
    _TRACKER   = tracker          # expose to /performance and /recent endpoints
    scanner    = Scanner(
        watchlist_store=watchlist,
        alert_callback=_no_op_alert,
        performance_tracker=tracker,
    )
    _SCANNER = scanner            # expose to /coins endpoint

    logger.info("MTB Scanner API: starting bootstrap...")
    try:
        await scanner.run_bootstrap()
        logger.info("MTB Scanner API: bootstrap complete")
    except Exception:
        logger.exception("MTB Scanner API: bootstrap failed — continuing without pre-loaded history")

    # First scan immediately after bootstrap
    logger.info("MTB Scanner API: starting scan loop")
    while True:
        try:
            tickers = await scanner.get_tickers(force=True)

            logger.info(f"Tickers Downloaded={len(tickers)}")

            scanner.evaluate_signal_performance(tickers)

            watchlist_sigs = await scanner.scan_watchlist(tickers)

            discovery_sigs = await scanner.scan_market(tickers)

            all_signals = watchlist_sigs + discovery_sigs

            logger.info(
                f"Watchlist={len(watchlist_sigs)} "
                f"Discovery={len(discovery_sigs)} "
                f"Total={len(all_signals)}"
            )

            fresh = []

            # Filter and convert to API-friendly dicts
            fresh: list[dict] = []
            for sig in all_signals:
                if not smart_filter(sig):
                    continue
                if not learning_filter(sig, tracker):
                    continue
                if not historical_filter(sig):
                    continue
                fresh.append({
                    "coin":             sig.coin,
                    "market_state":     sig.market_state,
                    "opportunity_type": sig.opportunity_type,
                    "priority":         sig.priority,
                    "score":            sig.opportunity_score,
                    "confidence":       sig.opp_confidence,
                    "tier":             sig.tier,
                    "final_score":      sig.final_score,
                    "risk_level":       sig.risk_level,
                    "price":            sig.price,
                    "coin_class":       sig.coin_class,
                    "timestamp":        sig.created_at.isoformat(),
                })

            LATEST_MTB_SIGNALS  = fresh
            logger.info(
                f"LIVE SIGNALS:{len(LATEST_MTB_SIGNALS)}"
            )
            _SCAN_CYCLES       += 1
            _SIGNALS_GENERATED += len(fresh)
            _LAST_SCAN_TIME     = datetime.now(timezone.utc).isoformat()

            # Aggregate market state from current price history
            state = scanner.aggregate_market_state()
            LATEST_MARKET_STATE = {
                "market_state": state,
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                "Scan done: %d signals, market_state=%s",
                len(fresh), state,
            )

        except Exception:
            logger.exception("Scanner loop error — retrying after interval")

        await asyncio.sleep(int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))


# =============================================================================
# GLOBAL EXCEPTION HANDLERS  [P2-SCN-V2.7B]
# =============================================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Normalise FastAPI HTTPException (404, 405, etc.) to our JSON shape."""
    logger.warning(
        "HTTP %s on %s %s: %s",
        exc.status_code, request.method, request.url.path, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status":    "error",
            "message":   str(exc.detail),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for any unhandled exception that escapes an endpoint.
    Logs the full traceback and returns a structured JSON error.
    All individual endpoints already have their own try/except, so this
    handler is a last-resort safety net only.
    """
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "status":    "error",
            "message":   str(exc) or "internal server error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return consistent JSON shape for FastAPI request-validation failures."""
    logger.warning(
        "Validation error on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=422,
        content={
            "status":    "error",
            "message":   str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# =============================================================================
# STARTUP SELF-TEST  [P2-SCN-V2.7A]
# =============================================================================

async def _run_startup_selftest() -> None:
    """
    Verify storage, required files, scanner instance, background loop,
    and registered routes.  Prints a human-readable summary to stdout/log.
    Failures are logged but never raise — the service starts regardless.
    """
    from pathlib import Path as _Path

    checks: dict[str, bool] = {}

    # ── 1. Storage writable ────────────────────────────────────────────────
    try:
        probe = _Path(STORAGE_DIR) / ".startup_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage_writable"] = True
    except Exception:
        logger.exception("Startup self-test: storage write failed")
        checks["storage_writable"] = False

    # ── 2. Required JSON files ──────────────────────────────────────────────
    for fname in ("watchlist.json", "signals.json", "stats.json"):
        checks[fname] = (_Path(STORAGE_DIR) / fname).is_file()

    # ── 3. Scanner instance and background loop ────────────────────────────
    # Create the task now and yield once so _scanner_loop runs up to its
    # first await — by then _SCANNER and _TRACKER are both assigned.
    global _SCANNER_TASK
    task = asyncio.create_task(_scanner_loop())
    logger.info(f"SCANNER TASK CREATED: {task}")
    _SCANNER_TASK = task
    await asyncio.sleep(0)          # yield → task runs to first await
    logger.info(
          f"TASK DONE={task.done()} "
          f"CANCELLED={task.cancelled()}"
    )
    checks["scanner_instance"] = _SCANNER is not None
    checks["background_loop"]  = not task.done()   # still alive

    # ── 4. API routes registered ───────────────────────────────────────────
    route_paths = [
        getattr(r, "path", "") for r in app.routes
    ]
    required_routes = {
        "/health",
        "/api/v1/scanner/signals",
        "/api/v1/scanner/market-state",
        "/api/v1/scanner/status",
        "/api/v1/scanner/metrics",
        "/api/v1/scanner/watchlist",
    }
    checks["api_routes"] = required_routes.issubset(set(route_paths))

    # ── Summary ────────────────────────────────────────────────────────────
    tick = lambda ok: "✅" if ok else "❌"

    storage_ok = (
        checks["storage_writable"]
        and checks["watchlist.json"]
        and checks["signals.json"]
        and checks["stats.json"]
    )
    scanner_ok = checks["scanner_instance"]
    loop_ok    = checks["background_loop"]
    api_ok     = checks["api_routes"]

    lines = [
        "🚀 Scanner V2 Started",
        f"   Storage Ready           {tick(storage_ok)}",
        f"     writable={checks['storage_writable']}  "
        f"watchlist.json={checks['watchlist.json']}  "
        f"signals.json={checks['signals.json']}  "
        f"stats.json={checks['stats.json']}",
        f"   Scanner Ready           {tick(scanner_ok)}",
        f"   Background Loop Ready   {tick(loop_ok)}",
        f"   API Ready               {tick(api_ok)}",
    ]
    for line in lines:
        print(line, flush=True)
        logger.info(line)

    all_ok = storage_ok and scanner_ok and loop_ok and api_ok
    if not all_ok:
        logger.warning("Startup self-test: one or more checks FAILED — see details above")
    else:
        logger.info("Startup self-test: all checks passed")


# =============================================================================
# PERSISTENCE HELPERS  [P2-SCN-V2.7C + V2.7D]
# =============================================================================

# Source-of-truth file paths (constructed from imported STORAGE_DIR)
_WATCHLIST_FILE = os.path.join(STORAGE_DIR, "watchlist.json")

# The three (source_path, backup_name) pairs used by both backup and shutdown
_BACKUP_PAIRS = [
    (SIGNAL_LOG_FILE,   "signals_backup.json"),
    (STATS_FILE,        "stats_backup.json"),
    (_WATCHLIST_FILE,   "watchlist_backup.json"),
]


async def _do_backup(*, label: str = "Backup") -> None:
    """
    Atomically copy each source file into BACKUP_DIR/<name>_backup.json.
    Write to a .tmp sidecar first, then os.replace() — crash-safe.
    Runs in a thread-pool executor so it never blocks the event loop.
    """
    import json as _json
    from pathlib import Path as _Path

    backup_dir = _Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, bool] = {}
    for src_path, dst_name in _BACKUP_PAIRS:
        short = dst_name.replace("_backup.json", "")
        dst   = backup_dir / dst_name
        tmp   = dst.with_suffix(".tmp")
        try:
            src = _Path(src_path)
            data = src.read_bytes() if src.is_file() else b"[]"
            await asyncio.get_event_loop().run_in_executor(
                None, lambda d=data, t=tmp, f=dst: (t.write_bytes(d), t.replace(f))
            )
            results[short] = True
        except Exception:
            logger.exception("%s: failed to write %s", label, dst_name)
            results[short] = False

    tick = lambda ok: "✅" if ok else "❌"
    logger.info("%s complete:", label)
    for name, ok in results.items():
        logger.info("  %-12s %s", name, tick(ok))
    if not all(results.values()):
        logger.warning("%s: one or more files failed — see above", label)


async def _backup_loop() -> None:
    """Hourly backup — sleeps first so it doesn't duplicate the startup check."""
    interval = int(os.getenv("BACKUP_INTERVAL_SECONDS", "3600"))
    logger.info("Backup loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await _do_backup(label="Hourly backup")
        except Exception:
            logger.exception("Backup loop: unexpected error in _do_backup")


async def _do_shutdown_save() -> None:
    """
    Called by the shutdown lifespan event.  Saves all three files,
    cancels background tasks, then logs completion.
    Never raises — errors are caught per-file.
    """
    from pathlib import Path as _Path
    import json as _json

    print("Scanner shutting down...", flush=True)
    logger.info("Scanner shutting down...")

    # ── 1. Save all files ──────────────────────────────────────────────────
    backup_dir = _Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    labels = {"signals_backup.json": "Signals",
              "stats_backup.json":   "Stats",
              "watchlist_backup.json": "Watchlist"}

    for src_path, dst_name in _BACKUP_PAIRS:
        dst = backup_dir / dst_name
        tmp = dst.with_suffix(".tmp")
        try:
            src  = _Path(src_path)
            data = src.read_bytes() if src.is_file() else b"[]"
            tmp.write_bytes(data)
            tmp.replace(dst)
            msg = f"{labels[dst_name]} saved ✅"
        except Exception:
            logger.exception("Shutdown save failed: %s", dst_name)
            msg = f"{labels[dst_name]} save failed ❌"
        print(msg, flush=True)
        logger.info(msg)

    # ── 2. Cancel background tasks ─────────────────────────────────────────
    for task_ref, name in ((_SCANNER_TASK, "scanner loop"),
                           (_BACKUP_TASK,  "backup loop")):
        if task_ref is not None and not task_ref.done():
            task_ref.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task_ref), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            logger.info("Stopped %s ✅", name)

    print("Shutdown complete ✅", flush=True)
    logger.info("Shutdown complete ✅")


# =============================================================================
# STARTUP EVENT
# =============================================================================

@app.on_event("startup")
async def startup_event():
    global _BACKUP_TASK
    await _run_startup_selftest()
    # _scanner_loop task is already created and stored in _SCANNER_TASK
    # inside _run_startup_selftest.  Start the hourly backup loop here.
    _BACKUP_TASK = asyncio.create_task(_backup_loop())
    logger.info("Scanner background task created")


@app.on_event("shutdown")
async def shutdown_event():
    await _do_shutdown_save()
def scanner_worker():

    while True:

        run_market_scan()

        time.sleep(300)

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting MTB Scanner API on 0.0.0.0:%d", port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
