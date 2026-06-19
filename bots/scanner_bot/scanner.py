"""
Scanner v1 logic — extracted verbatim from CryptoScanner_MTB notebook.
Only change: added `exch_perf_90d: Optional[float] = None` to Signal dataclass
to fix the AttributeError that caused historical_filter to crash and signals
to always return [].
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests


# =============================================================================
# CONFIGURATION
# =============================================================================

try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

STORAGE_DIR = BASE_DIR / "data"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_WATCHLIST = ["BTC", "SOL", "ETH", "ZEC", "XRP", "BNB"]

COINDCX_TICKER_URL  = "https://api.coindcx.com/exchange/ticker"
COINDCX_CANDLES_URL = "https://public.coindcx.com/market_data/candles"
REQUEST_TIMEOUT_SECONDS = 10
TICKER_CACHE_TTL_SECONDS = int(os.getenv("TICKER_CACHE_TTL_SECONDS", "20"))

WATCHLIST_FILE   = os.getenv("WATCHLIST_FILE",   str(STORAGE_DIR / "watchlist.json"))
SIGNAL_LOG_FILE  = os.getenv("SIGNAL_LOG_FILE",  str(STORAGE_DIR / "signals.json"))
STATS_FILE       = os.getenv("STATS_FILE",        str(STORAGE_DIR / "stats.json"))
SCANNER_LOG_FILE = os.getenv("SCANNER_LOG_FILE",  str(STORAGE_DIR / "scanner.log"))

QUOTE_PRIORITY = ("INR", "USDT")

SCAN_INTERVAL_SECONDS      = int(os.getenv("SCAN_INTERVAL_SECONDS",      "300"))
DISCOVERY_INTERVAL_SECONDS = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "900"))
DISCOVERY_MAX_COINS        = int(os.getenv("DISCOVERY_MAX_COINS",        "500"))
SCAN_CONCURRENCY           = int(os.getenv("SCAN_CONCURRENCY",           "50"))
BOOTSTRAP_CONCURRENCY      = int(os.getenv("BOOTSTRAP_CONCURRENCY",      "30"))
BOOTSTRAP_ENABLED          = os.getenv("BOOTSTRAP_ENABLED", "true").lower() != "false"
MIN_VOLUME_24H   = float(os.getenv("MIN_VOLUME_24H",   "500000"))
MIN_LIQUIDITY_24H = float(os.getenv("MIN_LIQUIDITY_24H", "1000000"))
MIN_PRICE        = float(os.getenv("MIN_PRICE",        "0.01"))
MAX_RESULTS      = int(os.getenv("MAX_RESULTS",        "10"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))

MODEL_VERSION = "v12.2"

COIN_CLASSES: dict[str, set] = {
    "A": {"BTC", "ETH", "BNB", "SOL", "XRP"},
    "B": {"LINK", "AVAX", "SUI", "APT", "ARB", "NEAR", "INJ", "RENDER"},
}


def get_coin_class(symbol: str) -> str:
    sym = symbol.upper().split("-")[-1].split("_")[0]
    if sym in COIN_CLASSES["A"]:
        return "A"
    if sym in COIN_CLASSES["B"]:
        return "B"
    return "C"


EMA_FAST_PERIOD     = 9
EMA_SLOW_PERIOD     = 21
PRICE_HISTORY_LIMIT = 120

MTF_5M_WINDOW  = int(os.getenv("MTF_5M_WINDOW",  "10"))
MTF_15M_WINDOW = int(os.getenv("MTF_15M_WINDOW", "24"))
MTF_1H_WINDOW  = int(os.getenv("MTF_1H_WINDOW",  "48"))

MOMENTUM_THRESHOLD_PERCENT = 3.0
VOLUME_SPIKE_MULTIPLIER    = 2.0
VOLUME_AVERAGE_PERIOD      = 20
VOLATILITY_LOOKBACK        = 20
VOLATILITY_SPIKE_MULTIPLIER = 1.8

EVALUATION_HORIZONS = {
    "1h":  60 * 60,
    "4h":  4 * 60 * 60,
    "24h": 24 * 60 * 60,
}

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scanner_bot")
_log_handler = logging.FileHandler(SCANNER_LOG_FILE, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(_log_handler)


# =============================================================================
# STORAGE HELPERS
# =============================================================================

def backup_file(path: Path) -> None:
    if not path.exists():
        return
    backup_path = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, backup_path)
    except OSError:
        pass


def write_json_safely(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_file(path)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_path.replace(path)


def ensure_storage_files() -> None:
    for name, default in {
        "watchlist.json": {"coins": DEFAULT_WATCHLIST},
        "signals.json":   {"signals": []},
        "stats.json":     {},
    }.items():
        path = STORAGE_DIR / name
        if not path.exists():
            write_json_safely(path, default)
    (STORAGE_DIR / "scanner.log").touch(exist_ok=True)
    logger.info("Storage ready: %s", STORAGE_DIR)


ensure_storage_files()


# =============================================================================
# WATCHLIST STORAGE
# =============================================================================

class WatchlistStore:
    def __init__(self, path: str = WATCHLIST_FILE):
        self.path = Path(path)
        self._coins = self._load()

    def _load(self) -> list[str]:
        if not self.path.exists():
            return list(DEFAULT_WATCHLIST)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read watchlist file; using defaults", exc_info=True)
            return list(DEFAULT_WATCHLIST)
        coins = data.get("coins", data if isinstance(data, list) else [])
        normalized = [str(coin).upper().strip() for coin in coins if str(coin).strip()]
        return list(dict.fromkeys(normalized)) or list(DEFAULT_WATCHLIST)

    def save(self) -> None:
        write_json_safely(self.path, {"coins": self._coins})

    def all(self) -> list[str]:
        return list(self._coins)

    def add(self, coin: str) -> bool:
        normalized = coin.upper().strip()
        if not normalized or normalized in self._coins:
            return False
        self._coins.append(normalized)
        self.save()
        return True

    def remove(self, coin: str) -> bool:
        normalized = coin.upper().strip()
        if normalized not in self._coins:
            return False
        self._coins.remove(normalized)
        self.save()
        return True


# =============================================================================
# SIGNAL DATACLASS
# =============================================================================

@dataclass(frozen=True)
class Signal:
    coin: str
    kind: str
    score: int
    message: str
    price: float
    volume: float
    created_at: datetime
    tier: str
    reasons: list
    volume_strength: float
    momentum_strength: float
    model_version: str = MODEL_VERSION
    phase5_trend: int = 0
    phase5_pullback: int = 0
    phase5_momentum: int = 0
    phase5_risk_reward: int = 0
    phase5_total: int = 0
    final_score: int = 0
    hist_trend_7d:   int = 0
    hist_trend_30d:  int = 0
    hist_trend_90d:  int = 0
    hist_sr_quality: int = 0
    hist_vol_score:  int = 0
    hist_total:      int = 0
    coin_class:      str = "C"
    market_state:    str = ""
    opportunity_type: str = ""
    opp_confidence:   int = 0
    opportunity_score: int = 0
    priority:          str = ""
    risk_level:        str = ""
    # BUG FIX: this field was missing, causing AttributeError in historical_filter
    exch_perf_90d: Optional[float] = None


# =============================================================================
# MATH HELPERS
# =============================================================================

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def ema(values: list, period: int) -> list:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def percent_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def average(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def volatility(prices: list) -> float:
    moves = [abs(percent_change(prices[i - 1], prices[i])) for i in range(1, len(prices))]
    return average(moves)


def trend_summary(history: list) -> dict:
    if len(history) < 2:
        return {"trend": "warming up", "move_percent": 0.0}
    prices = [item["price"] for item in history]
    lookback = min(10, len(prices) - 1)
    move = percent_change(prices[-lookback - 1], prices[-1])
    fast = ema(prices, EMA_FAST_PERIOD)[-1]
    slow = ema(prices, EMA_SLOW_PERIOD)[-1]
    if fast > slow and move > 0:
        trend = "uptrend"
    elif fast < slow and move < 0:
        trend = "downtrend"
    else:
        trend = "sideways"
    return {"trend": trend, "move_percent": move, "ema_fast": fast, "ema_slow": slow}


# =============================================================================
# HISTORICAL CANDLES / PATTERN SCORE
# =============================================================================

_candle_cache: dict[str, tuple[float, list]] = {}
CANDLE_CACHE_TTL = 3600


def _fetch_daily_candles(market_pair: str, days: int = 95) -> list:
    import time as _time
    now = _time.time()
    cached = _candle_cache.get(market_pair)
    if cached and now - cached[0] < CANDLE_CACHE_TTL:
        return cached[1]
    to_ts   = int(now)
    from_ts = to_ts - days * 86400
    try:
        resp = requests.get(
            COINDCX_CANDLES_URL,
            params={"pair": market_pair, "resolution": "1D", "from": from_ts, "to": to_ts},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            _candle_cache[market_pair] = (now, data)
            return data
    except Exception:
        logger.debug("Candle fetch failed for %s", market_pair, exc_info=True)
    return []


def _coin_to_pair(coin: str) -> list:
    coin = coin.upper()
    return [f"B-{coin}_USDT", f"B-{coin}_INR", f"B-{coin}_BTC"]


def _trend_score_from_closes(closes: list, max_pts: int = 25) -> int:
    if len(closes) < 2:
        return 0
    net_ret    = percent_change(closes[0], closes[-1])
    ret_score  = _clamp(net_ret / 60.0, -1.0, 1.0)
    up_days    = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    consistency = up_days / (len(closes) - 1)
    raw = (ret_score * 0.6 + (consistency * 2 - 1) * 0.4)
    normalised = (raw + 1) / 2
    return int(round(_clamp(normalised * max_pts, 0, max_pts)))


def _sr_quality_score(closes: list, current_price: float, max_pts: int = 25) -> int:
    if len(closes) < 10 or current_price <= 0:
        return 12
    band = 0.015
    levels: list = []
    for ref in closes:
        touches = sum(1 for c in closes if abs(c - ref) / ref <= band)
        if touches >= 3 and not any(abs(ref - lv) / ref <= band for lv in levels):
            levels.append(ref)
    if not levels:
        return 12
    supports    = [lv for lv in levels if lv <= current_price * 1.02]
    resistances = [lv for lv in levels if lv >  current_price * 1.02]
    support_proximity  = 0.0
    resistance_context = 0.0
    if supports:
        nearest_sup = max(supports)
        dist = abs(current_price - nearest_sup) / current_price
        support_proximity = _clamp(1.0 - dist / 0.05, 0.0, 1.0)
    if resistances:
        nearest_res = min(resistances)
        dist = (nearest_res - current_price) / current_price
        resistance_context = _clamp(1.0 - dist / 0.10, 0.0, 1.0) * 0.5
    raw = support_proximity * 0.7 + resistance_context
    return int(round(_clamp(raw * max_pts, 0, max_pts)))


def _hist_vol_score(closes: list, max_pts: int = 25) -> int:
    if len(closes) < 5:
        return 12
    avg_daily_move = average([abs(percent_change(closes[i-1], closes[i])) for i in range(1, len(closes))])
    ideal = 1.5
    deviation = abs(avg_daily_move - ideal) / ideal
    raw = _clamp(1.0 - deviation * 0.6, 0.0, 1.0)
    return int(round(_clamp(raw * max_pts, 0, max_pts)))


@dataclass(frozen=True)
class HistoricalPatternScore:
    trend_7d:   int
    trend_30d:  int
    trend_90d:  int
    sr_quality: int
    hist_vol:   int
    total:      int


def historical_pattern_score(coin: str, current_price: float) -> HistoricalPatternScore:
    candles: list = []
    for pair in _coin_to_pair(coin):
        candles = _fetch_daily_candles(pair, days=95)
        if candles:
            break
    if not candles:
        return HistoricalPatternScore(12, 12, 12, 12, 12, 60)
    candles = sorted(candles, key=lambda c: c.get("time", 0))
    closes  = [float(c.get("close", c.get("c", 0))) for c in candles if c.get("close", c.get("c", 0))]
    if len(closes) < 5:
        return HistoricalPatternScore(12, 12, 12, 12, 12, 60)
    trend_7d   = _trend_score_from_closes(closes[-7:]  if len(closes) >= 7  else closes)
    trend_30d  = _trend_score_from_closes(closes[-30:] if len(closes) >= 30 else closes)
    trend_90d  = _trend_score_from_closes(closes[-90:] if len(closes) >= 90 else closes)
    sr_quality = _sr_quality_score(closes, current_price)
    hist_vol   = _hist_vol_score(closes[-90:] if len(closes) >= 90 else closes)
    t7  = int(_clamp(trend_7d,   0, 25))
    t30 = int(_clamp(trend_30d,  0, 25))
    t90 = int(_clamp(trend_90d,  0, 25))
    sr  = int(_clamp(sr_quality, 0, 25))
    hv  = int(_clamp(hist_vol,   0, 25))
    total = int(_clamp(t7 + t30 + t90 + sr + hv, 0, 100))
    return HistoricalPatternScore(trend_7d=t7, trend_30d=t30, trend_90d=t90, sr_quality=sr, hist_vol=hv, total=total)


def get_historical_performance(coin: str) -> dict:
    def _pct(closes, n):
        window = closes[-(n + 1):]
        if len(window) < 2:
            return None
        o = window[0]; c = window[-1]
        return round((c - o) / o * 100, 4) if o > 0 else None

    for pair in _coin_to_pair(coin):
        candles = _fetch_daily_candles(pair, days=95)
        if not candles:
            continue
        candles_sorted = sorted(candles, key=lambda c: c.get("time", 0))
        closes = [
            float(c.get("close", c.get("c", 0)) or 0)
            for c in candles_sorted
            if float(c.get("close", c.get("c", 0)) or 0) > 0
        ]
        if len(closes) < 2:
            continue
        return {
            "coin":     coin,
            "perf_7d":  _pct(closes, 7),
            "perf_14d": _pct(closes, 14),
            "perf_30d": _pct(closes, 30),
            "perf_90d": _pct(closes, 90),
            "source":   pair,
            "error":    None,
        }
    return {"coin": coin, "perf_7d": None, "perf_14d": None, "perf_30d": None, "perf_90d": None, "source": None, "error": "no candle data available"}


# =============================================================================
# BOOTSTRAP
# =============================================================================

BOOTSTRAP_CANDLES_URL = COINDCX_CANDLES_URL
BOOTSTRAP_INTERVAL    = "5m"
BOOTSTRAP_LIMIT       = PRICE_HISTORY_LIMIT

_READY_EMA    = EMA_SLOW_PERIOD
_READY_MTF_5M = MTF_5M_WINDOW
_READY_MTF_15 = MTF_15M_WINDOW
_READY_MTF_1H = MTF_1H_WINDOW
_READY_P5     = 20


@dataclass
class BootstrapResult:
    coins_attempted: int = 0
    coins_loaded:    int = 0
    coins_failed:    int = 0
    avg_history_len: float = 0.0
    ema_ready:       bool = False
    mtf_ready:       bool = False
    phase5_ready:    bool = False
    duration_s:      float = 0.0
    failed_coins:    list = None

    def __post_init__(self):
        if self.failed_coins is None:
            self.failed_coins = []

    def summary_lines(self) -> list:
        return [
            "[Bootstrap] Startup history pre-load complete",
            f"  Coins attempted : {self.coins_attempted}",
            f"  Successfully loaded : {self.coins_loaded}",
            f"  Failed          : {self.coins_failed}",
            f"  Avg history len : {self.avg_history_len:.1f} ticks",
            f"  Ready for EMA   : {'YES' if self.ema_ready else 'NO'}",
            f"  Ready for MTF   : {'YES' if self.mtf_ready else 'NO'}",
            f"  Ready for Phase5: {'YES' if self.phase5_ready else 'NO'}",
            f"  Duration        : {self.duration_s:.1f}s",
        ]


def _bootstrap_pair_candidates(coin: str) -> list:
    coin = coin.upper()
    return [(f"B-{coin}_INR", "INR"), (f"B-{coin}_USDT", "USDT")]


def _fetch_bootstrap_candles(coin: str) -> list:
    for pair, _quote in _bootstrap_pair_candidates(coin):
        try:
            resp = requests.get(
                BOOTSTRAP_CANDLES_URL,
                params={"pair": pair, "interval": BOOTSTRAP_INTERVAL, "limit": BOOTSTRAP_LIMIT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            if isinstance(data, list) and len(data) >= 2:
                return data
        except Exception:
            logger.debug("Bootstrap candle fetch failed: coin=%s pair=%s", coin, pair, exc_info=True)
    return []


def _candles_to_history(candles: list) -> list:
    result = []
    for c in candles:
        try:
            close  = float(c.get("close",  c.get("c", 0)) or 0)
            volume = float(c.get("volume", c.get("v", 0)) or 0)
            ts_ms  = int(c.get("time",     c.get("t", 0)) or 0)
            if close <= 0 or ts_ms <= 0:
                continue
            result.append({
                "time":   datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "price":  close,
                "volume": volume,
            })
        except (TypeError, ValueError, KeyError):
            continue
    result.sort(key=lambda x: x["time"])
    return result[-PRICE_HISTORY_LIMIT:]


async def bootstrap_price_history(coins: list, price_history: dict, concurrency: int = BOOTSTRAP_CONCURRENCY) -> BootstrapResult:
    import time as _time
    t_start = _time.monotonic()
    sem     = asyncio.Semaphore(concurrency)
    results = {}

    async def _fetch_one(coin: str) -> None:
        if len(price_history.get(coin, [])) >= _READY_EMA:
            results[coin] = price_history[coin]
            return
        async with sem:
            candles = await asyncio.to_thread(_fetch_bootstrap_candles, coin)
        results[coin] = _candles_to_history(candles) if candles else None

    await asyncio.gather(*[_fetch_one(c) for c in coins], return_exceptions=False)

    loaded = 0; failed_coins = []; hist_lens = []
    for coin, history in results.items():
        if history:
            price_history[coin] = history
            loaded += 1
            hist_lens.append(len(history))
        else:
            failed_coins.append(coin)
            if len(failed_coins) <= 5:
                logger.warning("Bootstrap failed for coin=%s", coin)

    total   = len(coins)
    avg_len = sum(hist_lens) / len(hist_lens) if hist_lens else 0.0
    min_len = min(hist_lens) if hist_lens else 0
    t_end   = _time.monotonic()

    result = BootstrapResult(
        coins_attempted = total,
        coins_loaded    = loaded,
        coins_failed    = total - loaded,
        avg_history_len = avg_len,
        ema_ready       = min_len >= _READY_EMA,
        mtf_ready       = min_len >= _READY_MTF_1H,
        phase5_ready    = min_len >= _READY_P5,
        duration_s      = t_end - t_start,
        failed_coins    = failed_coins,
    )
    for line in result.summary_lines():
        logger.info(line)
    print("\n".join(result.summary_lines()))
    return result


# =============================================================================
# PHASE 5 QUALITY SCORING
# =============================================================================

@dataclass(frozen=True)
class Phase5Score:
    trend_quality: int
    pullback_quality: int
    momentum: int
    risk_reward: int
    total: int


def phase5_score(history: list) -> Phase5Score:
    prices  = [item["price"]  for item in history]
    volumes = [item["volume"] for item in history]
    if len(prices) < 6:
        return Phase5Score(0, 0, 0, 0, 0)

    window = prices[-20:] if len(prices) >= 20 else prices
    up_moves = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    consistency = up_moves / (len(window) - 1) if len(window) > 1 else 0.0
    fast_e = ema(prices, EMA_FAST_PERIOD)
    slow_e = ema(prices, EMA_SLOW_PERIOD)
    ema_sep = (fast_e[-1] - slow_e[-1]) / slow_e[-1] * 100 if slow_e[-1] else 0.0
    ema_sep_score = _clamp(ema_sep / 2.0, 0.0, 1.0)
    tq_raw = (consistency * 0.6 + ema_sep_score * 0.4) * 25
    trend_quality = int(round(_clamp(tq_raw, 0, 25)))

    pb_window = prices[-10:] if len(prices) >= 10 else prices
    if len(pb_window) >= 4:
        swing_high = max(pb_window[:-2])
        swing_low  = min(pb_window[1:-1])
        current_p  = pb_window[-1]
        prior_base = pb_window[0]
        leg_size = swing_high - prior_base
        if leg_size > 0 and swing_high > swing_low:
            retracement = (swing_high - swing_low) / leg_size
            ideal = 0.38
            deviation = abs(retracement - ideal) / ideal
            pb_raw = _clamp(1.0 - deviation, 0.0, 1.0)
            recovered = 1.0 if current_p > swing_low + (swing_high - swing_low) * 0.5 else 0.4
            pullback_quality = int(round(_clamp(pb_raw * recovered * 25, 0, 25)))
        else:
            pullback_quality = 0
    else:
        pullback_quality = 0

    roc_3 = percent_change(prices[-4], prices[-1]) if len(prices) >= 4 else 0.0
    roc_score = _clamp(roc_3 / 6.0, 0.0, 1.0)
    recent_vol = average(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
    base_vol   = average(volumes[-13:-3]) if len(volumes) >= 13 else average(volumes)
    vol_acc    = _clamp((recent_vol / base_vol - 1.0) / 2.0, 0.0, 1.0) if base_vol else 0.0
    acc = 0.0
    if len(prices) >= 3:
        move_now  = prices[-1] - prices[-2]
        move_prev = prices[-2] - prices[-3]
        if move_prev != 0:
            acc = _clamp(move_now / abs(move_prev) - 1.0, 0.0, 1.0)
    mom_raw = (roc_score * 0.5 + vol_acc * 0.3 + acc * 0.2) * 25
    momentum = int(round(_clamp(mom_raw, 0, 25)))

    rr_window   = prices[-15:] if len(prices) >= 15 else prices
    recent_low  = min(rr_window)
    recent_high = max(rr_window)
    cur = prices[-1]
    risk   = cur - recent_low  if cur > recent_low  else 0.0
    reward = recent_high - cur if recent_high > cur else (cur * 0.03)
    if risk > 0:
        rr_ratio = reward / risk
        rr_raw = _clamp(rr_ratio / 3.0, 0.0, 1.0) * 25
    else:
        rr_raw = 12.5
    risk_reward = int(round(_clamp(rr_raw, 0, 25)))

    total = trend_quality + pullback_quality + momentum + risk_reward
    return Phase5Score(trend_quality=trend_quality, pullback_quality=pullback_quality, momentum=momentum, risk_reward=risk_reward, total=total)


# =============================================================================
# MULTI-TIMEFRAME ANALYSIS
# =============================================================================

_mtf_counts: dict[str, int] = {"5m_only": 0, "15m_only": 0, "5m_15m": 0, "5m_15m_1h": 0, "none": 0}
_mtf_debug: dict[str, int] = {}
_mtf_failures: list = []


def _frame_bullish(all_prices: list, window: int) -> bool:
    if len(all_prices) < 2:
        return False
    slice_prices = all_prices[-window:] if len(all_prices) >= window else all_prices
    if len(slice_prices) < 2:
        return False
    if len(all_prices) >= EMA_SLOW_PERIOD:
        fast_vals = ema(all_prices, EMA_FAST_PERIOD)
        slow_vals = ema(all_prices, EMA_SLOW_PERIOD)
        ema_bullish = fast_vals[-1] > slow_vals[-1]
    else:
        ema_bullish = all_prices[-1] > all_prices[0]
    momentum_bullish = slice_prices[-1] > slice_prices[0]
    return ema_bullish and momentum_bullish


def multi_timeframe_check(history: list) -> dict:
    prices = [item["price"] for item in history]
    tf_5m  = _frame_bullish(prices, MTF_5M_WINDOW)
    tf_15m = _frame_bullish(prices, MTF_15M_WINDOW)
    tf_1h  = _frame_bullish(prices, MTF_1H_WINDOW)

    _mtf_debug["coins_checked"] = _mtf_debug.get("coins_checked", 0) + 1
    if len(prices) < MTF_5M_WINDOW:
        _mtf_debug["insufficient_history"] = _mtf_debug.get("insufficient_history", 0) + 1
    if tf_5m:  _mtf_debug["5m_bullish"]  = _mtf_debug.get("5m_bullish",  0) + 1
    if tf_15m: _mtf_debug["15m_bullish"] = _mtf_debug.get("15m_bullish", 0) + 1
    if tf_1h:  _mtf_debug["1h_bullish"]  = _mtf_debug.get("1h_bullish",  0) + 1
    if tf_5m and tf_15m and tf_1h:
        _mtf_debug["full_alignment"] = _mtf_debug.get("full_alignment", 0) + 1

    if tf_5m and tf_15m and tf_1h:
        alignment = "5m_15m_1h"
    elif tf_5m and tf_15m:
        alignment = "5m_15m"
    elif tf_5m and not tf_15m:
        alignment = "5m_only"
    elif tf_15m and not tf_5m:
        alignment = "15m_only"
    else:
        alignment = "none"

    _mtf_counts[alignment] = _mtf_counts.get(alignment, 0) + 1

    if alignment == "none" and len(_mtf_failures) < 10:
        _mtf_failures.append({
            "history_len": len(prices),
            "tf_5m": tf_5m, "tf_15m": tf_15m, "tf_1h": tf_1h,
            "last_price": prices[-1] if prices else None,
            "first_price": prices[0] if prices else None,
        })

    return {
        "tf_5m_bull":   tf_5m,
        "tf_15m_bull":  tf_15m,
        "tf_1h_bull":   tf_1h,
        "candidate_ok": tf_5m or tf_15m,
        "strong_ok":    tf_5m and tf_15m,
        "premium_ok":   tf_5m and tf_15m and tf_1h,
        "alignment":    alignment,
    }


# =============================================================================
# MARKET STATE ENGINE
# =============================================================================

def detect_market_state(history: list) -> str:
    if len(history) < 6:
        return "sideways"
    prices  = [item["price"]  for item in history]
    volumes = [item["volume"] for item in history]

    ema_bull = False
    if len(prices) >= EMA_SLOW_PERIOD:
        fast_e = ema(prices, EMA_FAST_PERIOD)
        slow_e = ema(prices, EMA_SLOW_PERIOD)
        ema_bull = fast_e[-1] > slow_e[-1]
    else:
        ema_bull = prices[-1] > prices[0]

    momentum_3 = percent_change(prices[-4], prices[-1]) if len(prices) >= 4 else percent_change(prices[0], prices[-1])
    momentum_positive = momentum_3 > 0.3
    momentum_negative = momentum_3 < -0.3

    recent_vol   = average(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
    baseline_vol = average(volumes[-13:-3]) if len(volumes) >= 13 else average(volumes)
    vol_ratio    = recent_vol / baseline_vol if baseline_vol else 1.0
    vol_spike    = vol_ratio > VOLUME_SPIKE_MULTIPLIER

    window = prices[-12:] if len(prices) >= 12 else prices
    n = len(window)
    first_half  = window[:n // 2]
    second_half = window[n // 2:]

    prev_high = max(first_half); prev_low = min(first_half)
    curr_high = max(second_half); curr_low = min(second_half)

    higher_highs = curr_high > prev_high
    higher_lows  = curr_low  > prev_low
    lower_highs  = curr_high < prev_high
    lower_lows   = curr_low  < prev_low

    range_size   = curr_high - curr_low
    pos_in_range = (prices[-1] - curr_low) / range_size if range_size > 0 else 0.5
    near_bottom  = pos_in_range < 0.25

    lookback      = prices[-20:] if len(prices) >= 20 else prices
    new_local_high = prices[-1] >= max(lookback)

    if new_local_high and vol_spike and momentum_positive:
        return "breakout"
    if ema_bull and higher_highs and higher_lows and momentum_positive:
        return "bull_trend"
    if ema_bull and near_bottom and not momentum_positive:
        return "pullback"
    if ema_bull and not (higher_highs and higher_lows) and momentum_positive:
        return "recovery"
    if not ema_bull and lower_highs and lower_lows and momentum_negative:
        return "downtrend"
    return "sideways"


# =============================================================================
# OPPORTUNITY TYPE ENGINE
# =============================================================================

_OPP_BASE: dict[str, str] = {
    "bull_trend": "continuation",
    "pullback":   "accumulation",
    "recovery":   "recovery_trade",
    "breakout":   "momentum_trade",
    "sideways":   "watchlist",
    "downtrend":  "avoid",
}

_OPP_LABELS: list = [
    "accumulation", "recovery_trade", "momentum_trade",
    "continuation", "watchlist", "avoid",
]

PRIORITY_LEVELS: list = [
    (90, "Elite"),
    (80, "High"),
    (70, "Medium"),
    (60, "Watch"),
    (0,  "Ignore"),
]

_CLASS_BONUS: dict[str, int] = {"A": 20, "B": 10, "C": 0}

_OPP_TYPE_BONUS: dict[str, int] = {
    "continuation":   15,
    "recovery_trade": 12,
    "accumulation":   10,
    "momentum_trade":  8,
    "watchlist":       0,
    "avoid":         -50,
}

_MTF_BONUS: dict[str, int] = {
    "5m_15m_1h": 20,
    "5m_15m":    10,
    "5m_only":    5,
    "15m_only":   5,
    "none":       0,
}


def calculate_risk_level(coin_class: str, opportunity_type: str, mtf_alignment: str, confidence: int) -> str:
    if opportunity_type == "avoid":
        return "high"
    full_mtf   = mtf_alignment == "5m_15m_1h"
    strong_mtf = mtf_alignment == "5m_15m"
    if coin_class == "A" and (full_mtf or strong_mtf):
        base = "low"
    elif coin_class == "A":
        base = "medium"
    elif coin_class == "B":
        base = "medium"
    elif opportunity_type == "momentum_trade":
        base = "high"
    else:
        base = "medium"
    if confidence < 60:
        if base == "low":
            return "medium"
        return "high"
    return base


def priority_from_score(opp_score: int) -> str:
    for threshold, label in PRIORITY_LEVELS:
        if opp_score >= threshold:
            return label
    return "Ignore"


def calculate_opportunity_score(coin_class: str, opportunity_type: str, confidence: int, mtf_alignment: str, historical_score: int) -> int:
    if opportunity_type == "avoid":
        return 0
    score = 30
    score += _CLASS_BONUS.get(coin_class, 0)
    score += _OPP_TYPE_BONUS.get(opportunity_type, 0)
    score += _MTF_BONUS.get(mtf_alignment, 0)
    score += min(confidence // 10, 10)
    score += min(historical_score // 10, 10)
    return max(0, min(100, score))


def detect_opportunity_type(market_state: str, coin_class: str, phase5_total: int, mtf_alignment: str) -> tuple:
    if market_state == "downtrend":
        return ("avoid", 0)
    opp_type   = _OPP_BASE.get(market_state, "watchlist")
    confidence = 40
    if coin_class == "A":
        confidence += 25
    elif coin_class == "B":
        confidence += 15
    if mtf_alignment == "5m_15m_1h":
        confidence += 20
    elif mtf_alignment == "5m_15m":
        confidence += 12
    elif mtf_alignment in ("5m_only", "15m_only"):
        confidence += 5
    if phase5_total >= 75:
        confidence += 15
    elif phase5_total >= 50:
        confidence += 8
    elif phase5_total >= 25:
        confidence += 3
    confidence = min(confidence, 100)
    return (opp_type, confidence)


# =============================================================================
# SMART FILTER / LEARNING FILTER / HISTORICAL FILTER
# =============================================================================

_filter_counts: dict[str, int] = {
    "low_score": 0, "no_volume": 0, "no_ema": 0, "no_mtf": 0,
    "smart_filter": 0, "learning_filter": 0, "historical_filter": 0,
}

_learning_avoid_keys: Optional[set] = None
_learning_recommend_keys: Optional[set] = None
_learning_cache_updated_at: float = 0.0
_LEARNING_CACHE_TTL = 3600.0


def smart_filter(signal: Signal) -> bool:
    reject = (
        signal.priority.lower() == "ignore"
        or signal.opportunity_type == "avoid"
        or (signal.risk_level.lower() == "high" and signal.opp_confidence < 60)
    )
    if reject:
        _filter_counts["smart_filter"] = _filter_counts.get("smart_filter", 0) + 1
        return False
    return True


def _build_learning_key(signal: Signal) -> str:
    return (
        signal.coin_class + "|"
        + signal.market_state + "|"
        + signal.opportunity_type + "|"
        + signal.priority
    )


def _matches_learning_key(signal_key: str, key_set: set) -> bool:
    if signal_key in key_set:
        return True
    base = "|".join(signal_key.split("|")[:3]) + "|*"
    return base in key_set


def _refresh_learning_cache(tracker) -> None:
    global _learning_avoid_keys, _learning_recommend_keys, _learning_cache_updated_at
    import time as _time
    now = _time.monotonic()
    if _learning_avoid_keys is not None and now - _learning_cache_updated_at < _LEARNING_CACHE_TTL:
        return

    def _key_from_desc(desc: str) -> str:
        try:
            parts = desc.split("-class ", 1)
            cls = parts[0].split()[-1].strip()
            rest = parts[1]
            ot_start = rest.find("("); ot_end = rest.find(")")
            ot_raw = rest[ot_start + 1:ot_end].replace(" ", "_")
            ms_raw = rest[:ot_start].strip().replace(" ", "_")
            return cls + "|" + ms_raw + "|" + ot_raw + "|*"
        except Exception:
            return ""

    recs = tracker.learning_recommendations()
    _learning_avoid_keys     = {_key_from_desc(d) for d in recs.get("avoid", [])}
    _learning_recommend_keys = {_key_from_desc(d) for d in recs.get("recommended", [])}
    _learning_cache_updated_at = now


def learning_filter(signal: Signal, tracker) -> bool:
    _refresh_learning_cache(tracker)
    if not _learning_avoid_keys:
        return True
    key = _build_learning_key(signal)
    if not key:
        return True
    in_avoid     = _matches_learning_key(key, _learning_avoid_keys)
    in_recommend = _matches_learning_key(key, _learning_recommend_keys or set())
    if in_avoid and not in_recommend:
        _filter_counts["learning_filter"] = _filter_counts.get("learning_filter", 0) + 1
        return False
    return True


HIST_FILTER_REJECT_BELOW = -50.0
HIST_FILTER_FLAG_ABOVE   = 100.0


def historical_filter(signal: Signal) -> bool:
    p90 = signal.exch_perf_90d  # now exists on Signal dataclass
    if p90 is None:
        return True
    if p90 < HIST_FILTER_REJECT_BELOW:
        _filter_counts["historical_filter"] = _filter_counts.get("historical_filter", 0) + 1
        logger.debug("historical_filter rejected %s: exch_perf_90d=%.2f%%", signal.coin, p90)
        return False
    if p90 > HIST_FILTER_FLAG_ABOVE:
        logger.debug("historical_filter flagged %s: exch_perf_90d=%.2f%% (parabolic)", signal.coin, p90)
    return True


# =============================================================================
# SIGNAL TIER / FORMATTERS
# =============================================================================

def signal_tier(score: int) -> str:
    if score >= 85: return "PREMIUM"
    if score >= 70: return "STRONG SIGNAL"
    if score >= 60: return "CANDIDATE"
    return "IGNORE"


def format_price(price: float) -> str:
    text = f"{price:,.8f}".rstrip("0").rstrip(".")
    return text if text else "0"


def format_volume(volume: float) -> str:
    return f"{volume:,.2f}"


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "pending"
    return f"{value:+.2f}%"


# =============================================================================
# ANALYZE COIN  (core signal generation — UNCHANGED)
# =============================================================================

def analyze_coin(coin: str, history: list) -> list:
    if len(history) < 2:
        return []

    mtf = multi_timeframe_check(history)
    if not mtf["candidate_ok"]:
        _filter_counts["no_mtf"] = _filter_counts.get("no_mtf", 0) + 1
        return []

    prices = [item["price"] for item in history]
    volumes = [item["volume"] for item in history]
    current_price  = prices[-1]
    current_volume = volumes[-1]
    created_at = datetime.now(timezone.utc)
    score = 0
    reasons: list = []

    fast = ema(prices, EMA_FAST_PERIOD)
    slow = ema(prices, EMA_SLOW_PERIOD)
    recent_move      = percent_change(prices[-2], current_price)
    momentum_strength = max(recent_move, 0.0)

    previous_volumes = volumes[-(VOLUME_AVERAGE_PERIOD + 1):-1]
    average_volume   = average(previous_volumes)
    volume_strength  = current_volume / average_volume if average_volume else 0.0

    # Gate 1: EMA crossover is MANDATORY
    has_ema_crossover = False
    if len(fast) >= 2 and len(slow) >= 2:
        crossed_up   = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
        if crossed_up or crossed_down:
            has_ema_crossover = True
            score += 25
            reasons.append("EMA crossover")
        if fast[-1] > slow[-1] and recent_move > 0:
            score += 10
            reasons.append("Strong trend")

    if not has_ema_crossover:
        _filter_counts["no_ema"] += 1
        return []

    # Gate 2: Volume spike is MANDATORY
    has_volume_spike = volume_strength > VOLUME_SPIKE_MULTIPLIER
    if has_volume_spike:
        score += 20
        reasons.append("Volume spike")
    else:
        _filter_counts["no_volume"] += 1
        return []

    if recent_move >= MOMENTUM_THRESHOLD_PERCENT:
        score += 15
        reasons.append("Positive momentum")

    if len(prices) > VOLATILITY_LOOKBACK + 1:
        current_volatility = volatility(prices[-(VOLATILITY_LOOKBACK + 1):])
        baseline = volatility(prices[-(VOLATILITY_LOOKBACK * 2 + 1):-VOLATILITY_LOOKBACK])
        if baseline and current_volatility > baseline * VOLATILITY_SPIKE_MULTIPLIER:
            score += 10
            reasons.append("High volatility breakout")

    # Gate 3: Minimum score
    if score < 60:
        _filter_counts["low_score"] += 1
        return []

    # MTF tier/bonus
    alignment = mtf["alignment"]
    if alignment == "5m_15m_1h":
        score = min(score + 10, 100)
        reasons.append("MTF: 5m+15m+1h aligned ⭐")
        effective_tier = signal_tier(score)
    elif alignment == "5m_15m":
        score = min(score + 5, 100)
        reasons.append("MTF: 5m+15m aligned")
        raw_tier = signal_tier(score)
        effective_tier = "STRONG SIGNAL" if raw_tier == "PREMIUM" else raw_tier
    elif alignment == "5m_only":
        reasons.append("MTF: 5m bullish only")
        effective_tier = "CANDIDATE"
    else:
        reasons.append("MTF: 15m bullish only")
        effective_tier = "CANDIDATE"

    tier         = effective_tier
    coin_class   = get_coin_class(coin)
    market_state = detect_market_state(history)
    p5           = phase5_score(history)
    hist         = historical_pattern_score(coin, current_price)

    scanner_norm = min(score, 100)
    final_score  = int(round(scanner_norm * 0.40 + p5.total * 0.40 + hist.total * 0.20))

    opportunity_type, opp_confidence = detect_opportunity_type(
        market_state=market_state,
        coin_class=coin_class,
        phase5_total=p5.total,
        mtf_alignment=mtf.get("alignment", "none"),
    )

    opportunity_score = calculate_opportunity_score(
        coin_class=coin_class,
        opportunity_type=opportunity_type,
        confidence=opp_confidence,
        mtf_alignment=mtf.get("alignment", "none"),
        historical_score=hist.total,
    )

    priority   = priority_from_score(opportunity_score)
    risk_level = calculate_risk_level(
        coin_class=coin_class,
        opportunity_type=opportunity_type,
        mtf_alignment=mtf.get("alignment", "none"),
        confidence=opp_confidence,
    )

    # Fetch 90-day exchange performance to populate the fixed field
    perf_data    = get_historical_performance(coin)
    exch_perf_90d = perf_data.get("perf_90d")

    return [
        Signal(
            coin=coin,
            kind=tier.lower().replace(" ", "_"),
            score=score,
            message="; ".join(reasons),
            price=current_price,
            volume=current_volume,
            created_at=created_at,
            tier=tier,
            reasons=reasons,
            volume_strength=volume_strength,
            momentum_strength=momentum_strength,
            model_version=MODEL_VERSION,
            phase5_trend=p5.trend_quality,
            phase5_pullback=p5.pullback_quality,
            phase5_momentum=p5.momentum,
            phase5_risk_reward=p5.risk_reward,
            phase5_total=p5.total,
            final_score=final_score,
            hist_trend_7d=hist.trend_7d,
            hist_trend_30d=hist.trend_30d,
            hist_trend_90d=hist.trend_90d,
            hist_sr_quality=hist.sr_quality,
            hist_vol_score=hist.hist_vol,
            hist_total=hist.total,
            coin_class=coin_class,
            market_state=market_state,
            opportunity_type=opportunity_type,
            opp_confidence=opp_confidence,
            opportunity_score=opportunity_score,
            priority=priority,
            risk_level=risk_level,
            exch_perf_90d=exch_perf_90d,
        )
    ]


# =============================================================================
# PUBLIC MARKET DATA CLIENT
# =============================================================================

class CoinDCXPublicClient:
    def fetch_tickers(self) -> list:
        response = requests.get(COINDCX_TICKER_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()


# =============================================================================
# SIGNAL PERFORMANCE TRACKER
# =============================================================================

class SignalPerformanceTracker:
    def __init__(self, path: str = SIGNAL_LOG_FILE, stats_path: str = STATS_FILE):
        self.path       = Path(path)
        self.stats_path = Path(stats_path)
        self._data      = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"signals": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"signals": []}
        if not isinstance(data, dict):
            return {"signals": []}
        data.setdefault("signals", [])
        return data

    def save(self) -> None:
        write_json_safely(self.path, self._data)

    def save_stats(self) -> None:
        try:
            write_json_safely(self.stats_path, self.stats())
        except Exception:
            pass

    def log_signal(self, signal: Signal) -> None:
        if self._is_duplicate(signal):
            return
        self._data["signals"].append({
            "id":         f"{signal.coin}-{int(signal.created_at.timestamp())}-{signal.kind}",
            "timestamp":  signal.created_at.isoformat(),
            "coin":       signal.coin,
            "category":   signal.tier.title(),
            "score":      signal.score,
            "signal_price": signal.price,
            "reasons":    signal.reasons,
            "model_version": signal.model_version,
            "phase5": {
                "trend_quality":    signal.phase5_trend,
                "pullback_quality": signal.phase5_pullback,
                "momentum":         signal.phase5_momentum,
                "risk_reward":      signal.phase5_risk_reward,
                "total":            signal.phase5_total,
            },
            "final_score":      signal.final_score,
            "coin_class":       signal.coin_class,
            "market_state":     signal.market_state,
            "opportunity_type": signal.opportunity_type,
            "opp_confidence":   signal.opp_confidence,
            "opportunity_score": signal.opportunity_score,
            "priority":         signal.priority,
            "risk_level":       signal.risk_level,
            "historical_score": {
                "trend_7d":   signal.hist_trend_7d,
                "trend_30d":  signal.hist_trend_30d,
                "trend_90d":  signal.hist_trend_90d,
                "sr_quality": signal.hist_sr_quality,
                "hist_vol":   signal.hist_vol_score,
                "total":      signal.hist_total,
            },
            "evaluations": {},
        })
        self.save()

    def evaluate_due_signals(self, prices: dict) -> int:
        now     = datetime.now(timezone.utc)
        updated = 0
        for item in self._data["signals"]:
            signal_time = self._parse_time(item.get("timestamp"))
            if signal_time is None:
                continue
            coin          = item.get("coin")
            current_price = prices.get(coin)
            if current_price is None or current_price <= 0:
                continue
            signal_price = self._to_float(item.get("signal_price"))
            if signal_price <= 0:
                continue
            evaluations = item.setdefault("evaluations", {})
            age = (now - signal_time).total_seconds()
            for label, seconds in EVALUATION_HORIZONS.items():
                if age >= seconds and label not in evaluations:
                    change = percent_change(signal_price, current_price)
                    evaluations[label] = {
                        "timestamp": now.isoformat(),
                        "price":     current_price,
                        "change_percent": change,
                    }
                    updated += 1
        if updated:
            self.save()
            self.save_stats()
        return updated

    def stats(self) -> dict:
        signals        = self._data["signals"]
        latest_changes = [self._latest_change(item) for item in signals]
        completed      = [c for c in latest_changes if c is not None]
        winners        = sum(1 for c in completed if c > 0)
        losers         = sum(1 for c in completed if c <= 0)
        return {
            "last_updated":   datetime.now(timezone.utc).isoformat(),
            "total_signals":  len(signals),
            "winning_signals": winners,
            "losing_signals":  losers,
            "win_rate":       (winners / len(completed) * 100) if completed else 0.0,
        }

    def learning_recommendations(self) -> dict:
        return {"recommended": [], "avoid": [], "has_data": False}

    def top_ranked_signals(self, limit: int = 10) -> list:
        ranked = []
        for item in self._data["signals"]:
            opp_sc = item.get("opportunity_score")
            if opp_sc is None:
                continue
            pri = item.get("priority", "Ignore")
            if pri.lower() == "ignore":
                continue
            ranked.append(item)
        ranked.sort(
            key=lambda s: (
                self._to_float(s.get("opportunity_score", 0)),
                self._to_float(s.get("opp_confidence", 0)),
                s.get("timestamp", ""),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def recent_signals(self, limit: int = 10) -> list:
        return sorted(self._data["signals"], key=lambda i: i.get("timestamp", ""), reverse=True)[:limit]

    def _latest_change(self, item: dict) -> Optional[float]:
        evaluations = item.get("evaluations", {})
        for label in ("24h", "4h", "1h"):
            if label in evaluations:
                return self._to_float(evaluations[label].get("change_percent"))
        return None

    def _is_duplicate(self, signal: Signal) -> bool:
        signal_minute = signal.created_at.replace(second=0, microsecond=0).isoformat()
        for item in self._data["signals"][-50:]:
            item_time = self._parse_time(item.get("timestamp"))
            if item_time is None:
                continue
            item_minute = item_time.replace(second=0, microsecond=0).isoformat()
            if item.get("coin") == signal.coin and item.get("score") == signal.score and item_minute == signal_minute:
                return True
        return False

    @staticmethod
    def _parse_time(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


# =============================================================================
# ASYNC SCANNER
# =============================================================================

class Scanner:
    def __init__(
        self,
        watchlist_store: WatchlistStore,
        alert_callback: Callable,
        performance_tracker: SignalPerformanceTracker,
        client: Optional[CoinDCXPublicClient] = None,
    ):
        self.watchlist_store     = watchlist_store
        self.alert_callback      = alert_callback
        self.performance_tracker = performance_tracker
        self.client              = client or CoinDCXPublicClient()

        self.price_history: dict[str, list] = defaultdict(list)
        self.last_alert_at: dict[str, datetime] = {}

        self._alert_in_flight: set  = set()
        self._alert_lock            = asyncio.Lock()
        self._scan_semaphore        = asyncio.Semaphore(SCAN_CONCURRENCY)

        self._ticker_cache: Optional[list] = None
        self._ticker_cache_at = 0.0
        self._ticker_lock = asyncio.Lock()

        self._bootstrap_result: Optional[BootstrapResult] = None

    async def run_bootstrap(self) -> BootstrapResult:
        if not BOOTSTRAP_ENABLED:
            logger.info("Bootstrap disabled (BOOTSTRAP_ENABLED=false)")
            self._bootstrap_result = BootstrapResult()
            return self._bootstrap_result

        logger.info("Bootstrap: fetching current tickers to build coin list...")
        try:
            tickers = await self.get_tickers(force=True)
        except Exception:
            logger.warning("Bootstrap: ticker fetch failed; skipping bootstrap", exc_info=True)
            self._bootstrap_result = BootstrapResult()
            return self._bootstrap_result

        ticker_map    = self._ticker_map(tickers)
        watchlist_set = set(self.watchlist_store.all())
        discovery_set = {
            coin for coin, ticker in ticker_map.items()
            if coin not in watchlist_set and self._passes_discovery_filters(ticker)
        }
        all_coins = list(watchlist_set) + list(discovery_set)[:DISCOVERY_MAX_COINS]
        logger.info("Bootstrap: loading history for %d coins", len(all_coins))

        result = await bootstrap_price_history(
            coins=all_coins,
            price_history=self.price_history,
            concurrency=BOOTSTRAP_CONCURRENCY,
        )
        self._bootstrap_result = result
        return result

    async def run_forever(self) -> None:
        discovery_due = 0.0
        logger.info(
            "Scanner started: scan_interval=%ss discovery_interval=%ss concurrency=%s",
            SCAN_INTERVAL_SECONDS, DISCOVERY_INTERVAL_SECONDS, SCAN_CONCURRENCY,
        )
        while True:
            started = asyncio.get_running_loop().time()
            try:
                tickers = await self.get_tickers(force=True)
                self.evaluate_signal_performance(tickers)
                watchlist_signals = await self.scan_watchlist(tickers)

                now = asyncio.get_running_loop().time()
                if now >= discovery_due:
                    discovery_signals = await self.scan_market(tickers)
                    discovery_due = now + DISCOVERY_INTERVAL_SECONDS
                else:
                    discovery_signals = []

                elapsed = asyncio.get_running_loop().time() - started
                logger.info(
                    "Scan complete: watchlist_signals=%s discovery_signals=%s elapsed=%.2fs",
                    len(watchlist_signals), len(discovery_signals), elapsed,
                )
            except Exception:
                logger.exception("Scanner loop failed; retrying next interval")

            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(5, SCAN_INTERVAL_SECONDS - elapsed))

    async def get_tickers(self, force: bool = False) -> list:
        async with self._ticker_lock:
            now = asyncio.get_running_loop().time()
            cache_fresh = (
                self._ticker_cache is not None
                and now - self._ticker_cache_at < TICKER_CACHE_TTL_SECONDS
            )
            if cache_fresh and not force:
                return self._ticker_cache or []
            try:
                tickers = await asyncio.to_thread(self.client.fetch_tickers)
            except (requests.RequestException, ValueError):
                if self._ticker_cache is not None:
                    logger.warning("Ticker fetch failed; using cached data", exc_info=True)
                    return self._ticker_cache
                raise
            self._ticker_cache    = tickers
            self._ticker_cache_at = asyncio.get_running_loop().time()
            return tickers

    def evaluate_signal_performance(self, tickers: list) -> None:
        ticker_map = self._ticker_map(tickers)
        prices = {
            coin: self._extract_price_volume(ticker)[0]
            for coin, ticker in ticker_map.items()
        }
        updated = self.performance_tracker.evaluate_due_signals(prices)
        if updated:
            logger.info("Updated %s signal performance checkpoints", updated)

    async def scan_watchlist(self, tickers: Optional[list] = None) -> list:
        tickers    = tickers or await self.get_tickers()
        ticker_map = self._ticker_map(tickers)
        coins      = [coin for coin in self.watchlist_store.all() if coin in ticker_map]
        return await self._scan_many(coins, ticker_map, source="watchlist")

    async def scan_market(self, tickers: Optional[list] = None) -> list:
        tickers    = tickers or await self.get_tickers()
        ticker_map = self._ticker_map(tickers)
        watchlist  = set(self.watchlist_store.all())
        coins = [
            coin for coin, ticker in ticker_map.items()
            if coin not in watchlist and self._passes_discovery_filters(ticker)
        ][:DISCOVERY_MAX_COINS]
        return await self._scan_many(coins, ticker_map, source="discovery")

    async def coin_snapshot(self, coin: str) -> Optional[dict]:
        coin    = coin.upper().strip()
        tickers = await self.get_tickers()
        ticker  = self._ticker_map(tickers).get(coin)
        if not ticker:
            return None
        price, volume = self._extract_price_volume(ticker)
        if price <= 0:
            return None
        self._append_history(coin, price, volume)
        history = self.price_history[coin]
        return {
            "coin": coin, "price": price, "volume": volume,
            "history_count": len(history),
            "trend": trend_summary(history),
            "signals": analyze_coin(coin, history),
        }

    async def _scan_many(self, coins: list, ticker_map: dict, source: str) -> list:
        tasks = [
            asyncio.create_task(self._scan_ticker_bounded(coin, ticker_map[coin], source))
            for coin in coins
        ]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals: list[Signal] = []
        for coin, result in zip(coins, results):
            if isinstance(result, Exception):
                logger.error("Coin scan failed: coin=%s source=%s", coin, source, exc_info=(type(result), result, result.__traceback__))
                continue
            signals.extend(result)

        top_signals = self._rank_signals(signals)[:MAX_RESULTS]
        for signal in top_signals:
            if not smart_filter(signal):
                continue
            if not learning_filter(signal, self.performance_tracker):
                continue
            if not historical_filter(signal):
                continue
            await self._send_alert_once(signal, source)
        return top_signals

    async def _scan_ticker_bounded(self, coin: str, ticker: dict, source: str) -> list:
        async with self._scan_semaphore:
            return await self._scan_ticker(coin, ticker, source)

    async def _scan_ticker(self, coin: str, ticker: dict, source: str) -> list:
        price, volume = self._extract_price_volume(ticker)
        if price <= 0:
            return []
        self._append_history(coin, price, volume)
        return analyze_coin(coin, self.price_history[coin])

    async def _send_alert_once(self, signal: Signal, source: str) -> None:
        async with self._alert_lock:
            if signal.coin in self._alert_in_flight:
                return
            if not self._cooldown_passed(signal.coin):
                return
            self._alert_in_flight.add(signal.coin)
        sent = False
        try:
            self.performance_tracker.log_signal(signal)
            await self.alert_callback(signal, source)
            sent = True
        except Exception:
            logger.exception("Failed to send alert for %s", signal.coin)
        finally:
            async with self._alert_lock:
                if sent:
                    self.last_alert_at[signal.coin] = datetime.now(timezone.utc)
                self._alert_in_flight.discard(signal.coin)
        if sent:
            logger.info("Alert sent: coin=%s kind=%s source=%s score=%s", signal.coin, signal.kind, source, signal.score)

    def _append_history(self, coin: str, price: float, volume: float) -> None:
        history = self.price_history[coin]
        history.append({
            "time":   datetime.now(timezone.utc),
            "price":  price,
            "volume": volume,
        })
        del history[:-PRICE_HISTORY_LIMIT]

    def _cooldown_passed(self, coin: str) -> bool:
        previous = self.last_alert_at.get(coin)
        if previous is None:
            return True
        age = (datetime.now(timezone.utc) - previous).total_seconds()
        return age >= ALERT_COOLDOWN_SECONDS

    def _ticker_map(self, tickers: list) -> dict:
        pairs: dict = {}
        priorities = {quote: index for index, quote in enumerate(QUOTE_PRIORITY)}
        selected_priority: dict = {}
        for ticker in tickers:
            market = str(ticker.get("market", "")).upper()
            for quote in QUOTE_PRIORITY:
                if market.endswith(quote) and len(market) > len(quote):
                    coin     = market[: -len(quote)]
                    priority = priorities[quote]
                    if coin not in pairs or priority < selected_priority[coin]:
                        pairs[coin]             = ticker
                        selected_priority[coin] = priority
                    break
        return pairs

    def _passes_discovery_filters(self, ticker: dict) -> bool:
        price, volume = self._extract_price_volume(ticker)
        if price < MIN_PRICE:
            return False
        if volume < MIN_VOLUME_24H:
            return False
        quote_vol = self._to_float(ticker.get("quote_volume") or ticker.get("volume_24h"))
        if quote_vol <= 0:
            quote_vol = volume * price
        if quote_vol < MIN_LIQUIDITY_24H:
            return False
        return True

    @staticmethod
    def _rank_signals(signals: list) -> list:
        return sorted(
            signals,
            key=lambda s: (s.final_score, s.phase5_total, s.hist_total, s.score),
            reverse=True,
        )

    def _extract_price_volume(self, ticker: dict) -> tuple:
        price  = self._to_float(ticker.get("last_price"))
        volume = self._to_float(
            ticker.get("volume") or ticker.get("volume_24h")
            or ticker.get("quote_volume") or ticker.get("base_volume")
        )
        return price, volume

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def aggregate_market_state(self) -> str:
        """Return the most common market state across coins with enough history."""
        counts: dict[str, int] = {}
        for coin, history in self.price_history.items():
            if len(history) < 6:
                continue
            state = detect_market_state(history)
            counts[state] = counts.get(state, 0) + 1
        if not counts:
            return "sideways"
        return max(counts, key=lambda k: counts[k])
