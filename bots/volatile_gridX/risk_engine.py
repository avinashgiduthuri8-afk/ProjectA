"""
PROJECT-A Risk Engine
"""

import time

from config import PHASE5
from storage import positions, watchlist
from market_data import analyze_coin

# ============================================================
# COOLDOWN
# ============================================================

cooldown_until = None

loss_streak = 0


def check_cooldown():

    global cooldown_until

    if cooldown_until:

        if time.time() < cooldown_until:

            remaining = int(

                cooldown_until - time.time()

            )

            return (

                True,

                f"Cooldown Active ({remaining}s)"

            )

    return False, "OK"


# ============================================================
# SIGNAL THRESHOLD
# ============================================================

def signal_threshold():

    return (

        PHASE5

        .get("signals", {})

        .get("min_score", 70)

    )


# ============================================================
# RISK CHECK
# ============================================================

def risk_check(score):

    max_positions = (

        PHASE5["trade"]

        .get("max_positions", 5)

    )

    if len(positions) >= max_positions:

        return (

            False,

            "Maximum Positions Reached"

        )

    if score < signal_threshold():

        return (

            False,

            "Score Below Threshold"

        )

    return True, "PASSED"


# ============================================================
# MARKET INTELLIGENCE
# ============================================================

def market_intelligence():

    proxy_coin = (

        watchlist[0]

        if watchlist

        else "BTC"

    )

    result = analyze_coin(

        proxy_coin

    )

    score = result.get(

        "score",

        0

    )

    if score >= 80:

        regime = "BULL"

    elif score >= 60:

        regime = "SIDEWAYS"

    else:

        regime = "BEAR"

    return {

        "regime": regime,

        "score": score

    }


# ============================================================
# MARKET FILTER
# ============================================================

def passes_market_intelligence_filter(

        coin

):

    market = market_intelligence()

    regime = market["regime"]

    if regime in [

        "BEAR",

        "HIGH_VOL"

    ]:

        return (

            False,

            f"{regime} Market"

        )

    return (

        True,

        "PASSED"

    )


# ============================================================
# POSITION CHECK
# ============================================================

def can_open_position(

        coin,

        score

):

    if coin in positions:

        return (

            False,

            "Position Already Exists"

        )

    cd, msg = check_cooldown()

    if cd:

        return (

            False,

            msg

        )

    risk_ok, reason = risk_check(

        score

    )

    if not risk_ok:

        return (

            False,

            reason

        )

    market_ok, reason = (

        passes_market_intelligence_filter(

            coin

        )

    )

    if not market_ok:

        return (

            False,

            reason

        )

    return (

        True,

        "APPROVED"

    )


# ============================================================
# VALIDATE SIGNAL
# ============================================================

def validate_signal(

        signal

):

    coin = (

        signal

        .get("coin", "")

        .upper()

    )

    action = (

        signal

        .get("action", "")

        .upper()

    )

    score = signal.get(

        "score",

        0

    )

    if action != "BUY":

        return (

            False,

            "BUY ONLY",

            signal

        )

    if coin not in watchlist:

        return (

            False,

            "Coin Not In Watchlist",

            signal

        )

    allowed, reason = (

        can_open_position(

            coin,

            score

        )

    )

    return (

        allowed,

        reason,

        signal

    )