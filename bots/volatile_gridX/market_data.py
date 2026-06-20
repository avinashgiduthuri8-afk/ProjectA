"""
PROJECT-A Market Data Engine
Price Cache + History + CoinDCX Fetcher
"""

import time
import requests

from config import buy_prices
import storage


# ============================================================
# MARKET CACHE
# ============================================================

market_cache = {}


# ============================================================
# UPDATE MARKET CACHE
# ============================================================

def update_market_cache():

    url = "https://api.coindcx.com/exchange/ticker"

    try:

        response = requests.get(

            url,

            timeout=10

        )

        if response.status_code != 200:

            return False

        tickers = response.json()

        for ticker in tickers:

            pair = ticker.get(

                "market",

                ""

            )

            for coin in storage.watchlist:

                if (

                    coin in pair

                    and

                    (

                        "INR" in pair

                        or

                        "USDT" in pair

                    )

                ):

                    try:

                        market_cache[coin] = {

                            "price":

                                float(

                                    ticker.get(

                                        "last_price",

                                        0

                                    )

                                ),

                            "volume":

                                float(

                                    ticker.get(

                                        "volume",

                                        0

                                    )

                                ),

                            "timestamp":

                                time.time()

                        }

                    except:

                        pass

        return True

    except:

        return False


# ============================================================
# GET PRICE
# ============================================================

def get_cached_price(

    coin

):

    return (

        market_cache

        .get(

            coin,

            {}

        )

        .get(

            "price",

            0

        )

    )


# ============================================================
# SAFE PRICE
# ============================================================

def get_cached_price_safe(

    coin

):

    price = (

        get_cached_price(

            coin

        )

    )

    if price > 0:

        return price

    return float(

        buy_prices.get(

            coin,

            0

        )

    )


# ============================================================
# PRICE HISTORY
# ============================================================

def update_price_history(

    coin,

    current_price

):

    if coin not in storage.price_history:

        storage.price_history[coin] = []

    storage.price_history[coin].append({

        "timestamp":

            time.time(),

        "price":

            current_price

    })

    MAX_HISTORY = 200

    if (

        len(

            storage.price_history[coin]

        )

        >

        MAX_HISTORY

    ):

        storage.price_history[coin] = (

            storage.price_history[coin]

            [-MAX_HISTORY:]

        )