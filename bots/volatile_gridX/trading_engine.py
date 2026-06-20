"""
PROJECT-A Trading Engine
Handles BUY execution and position management.
"""

import time
from datetime import datetime

from config import PHASE5
import storage
from market_data import get_cached_price_safe


# ============================================================
# CREATE POSITION
# ============================================================

def open_position(
    coin: str,
    price: float,
    amount: float,
    source: str = "SCANNER"
):

    qty = amount / price

    return {

        "coin": coin,

        "buy_price": price,

        "qty": qty,

        "amount": amount,

        "time": time.time(),

        "peak": price,

        "trailing_active": False,

        "trade_source": source

    }


# ============================================================
# BUY POSITION
# ============================================================

def buy_position(

    coin,

    price,

    amount,

    source="SCANNER"

):

    pos_key = f"{coin}_{source}"

    if pos_key in storage.positions:

        return False

    if storage.virtual_balance < amount:

        return False

    storage.virtual_balance -= amount

    storage.positions[pos_key] = open_position(

        coin,

        price,

        amount,

        source

    )

    return True


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(

    pos_key,

    current_price

):

    if pos_key not in storage.positions:

        return 0, 0, None

    pos = storage.positions[pos_key]

    qty = pos["qty"]

    receive_amount = qty * current_price

    pnl = receive_amount - pos["amount"]

    source = pos["trade_source"]

    storage.virtual_balance += receive_amount

    del storage.positions[pos_key]

    return receive_amount, pnl, source


# ============================================================
# PAPER EXECUTION
# ============================================================

def paper_execute_signal(signal):

    if signal["action"] != "BUY":

        return False, "BUY Only"

    coin = signal["coin"]

    source = signal.get(

        "source",

        "SCANNER"

    )

    price = get_cached_price_safe(

        coin

    )

    if price <= 0:

        return False, "Invalid Price"

    amount = (

        storage.virtual_balance

        *

        PHASE5["trade"]["buy_percent"]

    )

    success = buy_position(

        coin,

        price,

        amount,

        source

    )

    if not success:

        return (

            False,

            "Duplicate Position or Balance Low"

        )

    trade_entry = {

        "time":

            datetime.now()

            .strftime(

                "%Y-%m-%d %H:%M:%S"

            ),

        "coin": coin,

        "action":

            f"BUY [{source}]",

        "price": round(price, 2),

        "amount": round(amount, 2),

        "pnl": 0,

        "trade_source": source

    }

    storage.trade_log.append(

        trade_entry

    )

    storage.save_data()

    return (

        True,

        f"{coin} BUY Executed"

    )