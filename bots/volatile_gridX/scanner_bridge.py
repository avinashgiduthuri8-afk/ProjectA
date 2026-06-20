"""
PROJECT-A Scanner Bridge
Scanner Bot → Trading Bot Connector
"""

import time

from config import PHASE5
from storage import watchlist
from risk_engine import validate_signal
from trading_engine import paper_execute_signal


# ============================================================
# SIGNAL QUEUES
# ============================================================

signal_queue = []

scanner_rejections = []


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
# NORMALIZE SIGNAL
# ============================================================

def normalize_signal(signal):

    if not isinstance(signal, dict):

        return None

    return {

        "coin":

            str(

                signal.get(

                    "coin",

                    ""

                )

            ).upper(),

        "action":

            str(

                signal.get(

                    "action",

                    ""

                )

            ).upper(),

        "score":

            float(

                signal.get(

                    "score",

                    0

                )

            ),

        "source":

            signal.get(

                "source",

                "SCANNER"

            ),

        "timestamp":

            signal.get(

                "timestamp",

                time.time()

            )

    }


# ============================================================
# PROCESS SIGNAL
# ============================================================

def process_scanner_signal(signal):

    signal = normalize_signal(

        signal

    )

    if signal is None:

        return {

            "result": "REJECTED",

            "reason": "Invalid Payload"

        }

    coin = signal["coin"]

    action = signal["action"]

    score = signal["score"]

    # BUY ONLY

    if action != "BUY":

        reason = (

            "Only BUY Signals Allowed"

        )

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }

    # WATCHLIST FILTER

    if coin not in watchlist:

        reason = (

            "Coin Not In Watchlist"

        )

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }

    # VALIDATE SIGNAL

    accepted, reason, record = (

        validate_signal(

            signal

        )

    )

    if not accepted:

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }

    # EXECUTE TRADE

    executed, message = (

        paper_execute_signal(

            signal

        )

    )

    if executed:

        return {

            "result": "ACCEPTED",

            "reason": message

        }

    scanner_rejections.append({

        "coin": coin,

        "reason": message,

        "time": time.time()

    })

    return {

        "result": "REJECTED",

        "reason": message

    }


# ============================================================
# LEGACY ENTRY POINT
# ============================================================

def receive_signal(signal):

    return process_scanner_signal(

        signal

    )