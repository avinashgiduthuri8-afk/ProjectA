"""
PROJECT-A Persistent Storage Engine
"""

import os
import json
import shutil
import time

from config import *

# ============================================================
# RUNTIME VARIABLES
# ============================================================

virtual_balance = 1000000

positions = {}

trade_log = []

watchlist = list(PHASE5["coins"])

price_history = {}

market_cache = {}

portfolio_history = []

trade_history = []

error_logs = []

metrics_summary = {}

# ============================================================
# STORAGE STATUS
# ============================================================

storage_state = {

    "status": "INITIALIZED",

    "last_sync": 0,

    "sync_count": 0,

    "backup_status": "NONE"

}


# ============================================================
# VERIFY FILE
# ============================================================

def _verify_file(path):

    if not os.path.exists(path):
        return False

    if os.path.getsize(path) == 0:
        return False

    try:

        with open(path, "r") as f:

            json.load(f)

        return True

    except:

        return False


# ============================================================
# NORMALIZE STORAGE
# ============================================================

def _normalise(data):

    defaults = {

        "virtual_balance": 1000000,

        "positions": {},

        "trade_log": [],

        "watchlist": list(PHASE5["coins"]),

        "price_history": {},

        "market_cache": {},

        "portfolio_history": [],

        "trade_history": [],

        "error_logs": [],

        "metrics_summary": {}

    }

    for k, v in defaults.items():

        data.setdefault(k, v)

    return data


# ============================================================
# LOAD STORAGE
# ============================================================

def load_data():

    global virtual_balance

    global positions

    global trade_log

    global watchlist

    global price_history

    global market_cache

    global portfolio_history

    global trade_history

    global error_logs

    global metrics_summary


    if not _verify_file(STORAGE_FILE):

        os.makedirs(STORAGE_DIR, exist_ok=True)

        save_data()

        return


    with open(STORAGE_FILE, "r") as f:

        data = json.load(f)

    data = _normalise(data)

    virtual_balance = data["virtual_balance"]

    positions = data["positions"]

    trade_log = data["trade_log"]

    watchlist = data["watchlist"]

    price_history = data["price_history"]

    market_cache = data["market_cache"]

    portfolio_history = data["portfolio_history"]

    trade_history = data["trade_history"]

    error_logs = data["error_logs"]

    metrics_summary = data["metrics_summary"]


# ============================================================
# SAVE STORAGE
# ============================================================

def save_data():

    os.makedirs(STORAGE_DIR, exist_ok=True)

    payload = {

        "virtual_balance": virtual_balance,

        "positions": positions,

        "trade_log": trade_log,

        "watchlist": watchlist,

        "price_history": price_history,

        "market_cache": market_cache,

        "portfolio_history": portfolio_history,

        "trade_history": trade_history,

        "error_logs": error_logs,

        "metrics_summary": metrics_summary

    }

    temp_file = STORAGE_FILE + ".tmp"

    with open(temp_file, "w") as f:

        json.dump(payload, f, indent=4)

    if os.path.exists(STORAGE_FILE):

        shutil.copy2(

            STORAGE_FILE,

            STORAGE_BACKUP

        )

    os.replace(temp_file, STORAGE_FILE)

    storage_state["status"] = "SYNCED"

    storage_state["last_sync"] = time.time()

    storage_state["sync_count"] += 1