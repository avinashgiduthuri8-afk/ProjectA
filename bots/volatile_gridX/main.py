"""
PROJECT-A Trading Bot
Railway Production Main File
"""

import asyncio
import nest_asyncio
import atexit

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler
)

# ============================================================
# CORE MODULES
# ============================================================

from config import *
import storage

from analytics import (
    stats_cmd,
    history_cmd,
    analytics_cmd,
    update_stats
)

from telegram_handlers import (
    start_cmd,
    help_cmd,
    buy_cmd,
    sell_cmd,
    watchlist_cmd,
    addcoin_cmd,
    removecoin_cmd,
    mode_cmd,
    setmode_cmd,
    threshold_cmd,
    setthreshold_cmd
)

from market_data import (
    update_market_cache
)

from alerts import auto_alerts

from exit_engine import auto_sell


# ============================================================
# NEST ASYNC
# ============================================================

nest_asyncio.apply()


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("================================")

    print("PROJECT-A STARTING")

    print("Loading Storage...")

    storage.load_data()

    print(

        f"Balance : ₹{storage.virtual_balance}"

    )

    print(

        f"Watchlist : {storage.watchlist}"

    )

    print("Startup Complete")

    print("================================")


# ============================================================
# BACKGROUND LOOP
# ============================================================

async def background_loop():

    print("Background Engine Started")

    while True:

        try:

            # MARKET CACHE

            update_market_cache()

            # ALERT ENGINE

            await auto_alerts()

            # AUTO EXIT ENGINE

            auto_sell()

            # UPDATE ANALYTICS

            update_stats()

            # SAVE

            storage.save_data()

        except Exception as e:

            print(

                "[BACKGROUND ERROR]",

                e

            )

        await asyncio.sleep(

            STORAGE_SYNC_INTERVAL

        )


# ============================================================
# POST INIT
# ============================================================

async def post_init(app):

    asyncio.create_task(

        background_loop()

    )


# ============================================================
# MAIN
# ============================================================

def main():

    startup()

    app = (

        ApplicationBuilder()

        .token(BOT_TOKEN)

        .build()

    )

    app.post_init = post_init


    # ======================================
    # COMMANDS
    # ======================================

    app.add_handler(

        CommandHandler(

            "start",

            start_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "help",

            help_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "buy",

            buy_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "sell",

            sell_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "watchlist",

            watchlist_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "addcoin",

            addcoin_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "removecoin",

            removecoin_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "mode",

            mode_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "setmode",

            setmode_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "threshold",

            threshold_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "setthreshold",

            setthreshold_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "stats",

            stats_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "history",

            history_cmd

        )

    )

    app.add_handler(

        CommandHandler(

            "analytics",

            analytics_cmd

        )

    )


    # SAVE ON EXIT

    atexit.register(

        storage.save_data

    )


    print(

        "🚀 PROJECT-A LIVE"

    )

    app.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()