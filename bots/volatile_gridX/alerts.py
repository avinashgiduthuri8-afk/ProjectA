"""
PROJECT-A Alerts Engine
EMA Alerts + Telegram Notifications
"""

import time
import os
import requests

from config import BOT_TOKEN
import storage
from market_data import (
    get_cached_price_safe,
    update_price_history
)


# ============================================================
# ALERT FORMATTER
# ============================================================

def format_telegram_alert(

    title,

    coin,

    level,

    details

):

    emoji = {

        "INFO": "🟢",

        "WARNING": "⚠️",

        "ERROR": "🚨"

    }.get(level, "📢")

    return (

        f"{emoji} {title}\n\n"

        f"Coin: {coin}\n"

        f"Level: {level}\n"

        f"{details}\n"

        f"Time: "

        f"{time.strftime('%H:%M:%S')}"

    )


# ============================================================
# SEND TELEGRAM ALERT
# ============================================================

def dispatch_alert_payload(

    text

):

    chat_id = os.environ.get(

        "TELEGRAM_CHAT_ID",

        ""

    )

    if not chat_id:

        return

    url = (

        f"https://api.telegram.org/bot"

        f"{BOT_TOKEN}/sendMessage"

    )

    payload = {

        "chat_id": chat_id,

        "text": text

    }

    try:

        requests.post(

            url,

            json=payload,

            timeout=5

        )

    except:

        pass


# ============================================================
# AUTO ALERTS
# ============================================================

async def auto_alerts():

    for coin in list(

        storage.watchlist

    ):

        price = (

            get_cached_price_safe(

                coin

            )

        )

        if price <= 0:

            continue

        update_price_history(

            coin,

            price

        )

        history = (

            storage

            .price_history

            .get(

                coin,

                []

            )

        )

        if len(history) < 20:

            continue

        prices = [

            x["price"]

            for x in history

        ]

        ema_short = (

            sum(prices[-5:])

            / 5

        )

        ema_long = (

            sum(prices[-20:])

            / 20

        )

        # BUY SIGNAL

        if ema_short > ema_long:

            msg = (

                format_telegram_alert(

                    "BUY SIGNAL",

                    coin,

                    "INFO",

                    (

                        f"EMA5 "

                        f"{round(ema_short,2)} "

                        f"> EMA20 "

                        f"{round(ema_long,2)}"

                    )

                )

            )

            dispatch_alert_payload(

                msg

            )

        # POSITION ALERT

        scanner_key = (

            f"{coin}_SCANNER"

        )

        if scanner_key in storage.positions:

            pos = (

                storage.positions

                [scanner_key]

            )

            pnl_pct = (

                (

                    price

                    -

                    pos["buy_price"]

                )

                /

                pos["buy_price"]

            ) * 100

            if pnl_pct >= 5:

                msg = (

                    format_telegram_alert(

                        "TARGET HIT",

                        coin,

                        "INFO",

                        (

                            f"Profit "

                            f"{round(pnl_pct,2)}%"

                        )

                    )

                )

                dispatch_alert_payload(

                    msg

                )