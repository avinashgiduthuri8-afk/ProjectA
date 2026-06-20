"""
PROJECT-A Trading Bot Configuration
Railway Production Ready
"""

import os

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

PROJECT_NAME = "TradingBotCrypto"

# ============================================================
# PHASE 5 CONFIG
# ============================================================

PHASE5 = {

    "coins": [
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
        "ZEC"
    ],

    "trade": {

        "buy_percent": 0.10,

        "target_percent": 0.05,

        "stop_loss_percent": 0.05,

        "max_positions": 5

    },

    "signals": {

        "min_score": 70

    },

    "risk": {

        "safe": 0.20,

        "moderate": 0.40,

        "aggressive": 0.70,

        "active_profile": "MODERATE"

    }

}

# ============================================================
# FALLBACK PRICES
# ============================================================

buy_prices = {

    "BTC": 9000000,

    "ETH": 200000,

    "SOL": 8500,

    "BNB": 50000,

    "XRP": 50,

    "ZEC": 3200

}

# ============================================================
# STORAGE
# ============================================================

STORAGE_DIR = "storage"

STORAGE_FILE = os.path.join(
    STORAGE_DIR,
    f"{PROJECT_NAME}.json"
)

STORAGE_BACKUP = os.path.join(
    STORAGE_DIR,
    f"{PROJECT_NAME}_backup.json"
)

STORAGE_SYNC_INTERVAL = 30