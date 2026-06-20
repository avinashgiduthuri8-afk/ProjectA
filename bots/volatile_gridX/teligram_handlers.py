"""
PROJECT-A Telegram Command Handlers
"""

from config import PHASE5
import storage
import analytics
import trading_engine
import scanner_bridge
from market_data import get_cached_price_safe


# ============================================================
# START
# ============================================================

async def start_cmd(update, context):

    msg = (
        "🚀 PROJECT-A Trading Bot Online\n\n"
        "/help - Show Commands\n"
        "/buy <coin> <amount>\n"
        "/sell <coin>\n"
        "/watchlist\n"
        "/addcoin <coin>\n"
        "/removecoin <coin>\n"
        "/stats\n"
        "/history\n"
        "/analytics"
    )

    await update.message.reply_text(msg)


# ============================================================
# HELP
# ============================================================

async def help_cmd(update, context):

    msg = """

📌 COMMANDS

/buy BTC 1000
/sell BTC

/watchlist
/addcoin DOGE
/removecoin DOGE

/stats
/history
/analytics

/mode
/setmode aggressive

/threshold
/setthreshold 75

"""

    await update.message.reply_text(msg)


# ============================================================
# BUY
# ============================================================

async def buy_cmd(update, context):

    if len(context.args) < 2:

        await update.message.reply_text(

            "Usage:\n/buy BTC 1000"

        )

        return

    coin = context.args[0].upper()

    amount = float(

        context.args[1]

    )

    price = get_cached_price_safe(

        coin

    )

    success = (

        trading_engine.buy_position(

            coin,

            price,

            amount,

            source="MANUAL"

        )

    )

    if success:

        analytics.log_trade(

            coin,

            "BUY [MANUAL]",

            price,

            amount,

            pnl=0

        )

        await update.message.reply_text(

            f"✅ Bought {coin}"

        )

    else:

        await update.message.reply_text(

            "❌ Buy Failed"

        )


# ============================================================
# SELL
# ============================================================

async def sell_cmd(update, context):

    if len(context.args) < 1:

        return

    coin = (

        context.args[0]

        .upper()

    )

    key = f"{coin}_MANUAL"

    price = (

        get_cached_price_safe(

            coin

        )

    )

    receive, pnl, source = (

        trading_engine.close_position(

            key,

            price

        )

    )

    analytics.log_trade(

        coin,

        "SELL [MANUAL]",

        price,

        receive,

        pnl

    )

    await update.message.reply_text(

        f"✅ Sold {coin}\n"

        f"PnL ₹{round(pnl,2)}"

    )


# ============================================================
# WATCHLIST
# ============================================================

async def watchlist_cmd(

    update,

    context

):

    msg = "📋 WATCHLIST\n\n"

    for coin in storage.watchlist:

        price = (

            get_cached_price_safe(

                coin

            )

        )

        msg += (

            f"{coin}"

            f"  ₹{round(price,2)}\n"

        )

    await update.message.reply_text(

        msg

    )


# ============================================================
# ADD COIN
# ============================================================

async def addcoin_cmd(

    update,

    context

):

    if not context.args:

        return

    coin = (

        context.args[0]

        .upper()

    )

    if coin not in storage.watchlist:

        storage.watchlist.append(

            coin

        )

        storage.save_data()

    await update.message.reply_text(

        f"✅ {coin} Added"

    )


# ============================================================
# REMOVE COIN
# ============================================================

async def removecoin_cmd(

    update,

    context

):

    if not context.args:

        return

    coin = (

        context.args[0]

        .upper()

    )

    if coin in storage.watchlist:

        storage.watchlist.remove(

            coin

        )

        storage.save_data()

    await update.message.reply_text(

        f"❌ {coin} Removed"

    )


# ============================================================
# MODE
# ============================================================

async def mode_cmd(

    update,

    context

):

    mode = (

        PHASE5["risk"]

        .get(

            "active_profile",

            "MODERATE"

        )

    )

    await update.message.reply_text(

        f"Current Mode:\n{mode}"

    )


# ============================================================
# SET MODE
# ============================================================

async def setmode_cmd(

    update,

    context

):

    if not context.args:

        return

    mode = (

        context.args[0]

        .lower()

    )

    if mode in [

        "safe",

        "moderate",

        "aggressive"

    ]:

        PHASE5["risk"][

            "active_profile"

        ] = mode.upper()

        await update.message.reply_text(

            f"✅ Mode = {mode}"

        )


# ============================================================
# THRESHOLD
# ============================================================

async def threshold_cmd(

    update,

    context

):

    score = (

        scanner_bridge

        .signal_threshold()

    )

    await update.message.reply_text(

        f"Signal Threshold:\n{score}"

    )


# ============================================================
# SET THRESHOLD
# ============================================================

async def setthreshold_cmd(

    update,

    context

):

    if not context.args:

        return

    score = int(

        context.args[0]

    )

    PHASE5["signals"][

        "min_score"

    ] = score

    await update.message.reply_text(

        f"✅ Threshold Updated\n{score}"

    )