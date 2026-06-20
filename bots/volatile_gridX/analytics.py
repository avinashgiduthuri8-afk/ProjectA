"""
PROJECT-A Analytics Engine
Unified Stats + History + Analytics
"""

from datetime import datetime

import storage


# ============================================================
# TRADE STATS
# ============================================================

trade_stats = {

    "wins": 0,

    "losses": 0,

    "total_trades": 0,

    "total_profit": 0,

    "win_rate": 0,

    "best_trade": 0,

    "worst_trade": 0,

    "best_coin": None,

    "worst_coin": None

}


# ============================================================
# UPDATE STATS
# ============================================================

def update_stats():

    completed = [

        x

        for x in storage.trade_log

        if "SELL"

        in x.get(

            "action",

            ""

        ).upper()

    ]

    wins = 0

    losses = 0

    total_profit = 0

    best_trade = 0

    worst_trade = 0

    coin_pnl = {}

    for t in completed:

        pnl = t.get(

            "pnl",

            0

        )

        coin = t.get(

            "coin",

            "UNKNOWN"

        )

        total_profit += pnl

        coin_pnl[coin] = (

            coin_pnl.get(

                coin,

                0

            )

            + pnl

        )

        if pnl > 0:

            wins += 1

        elif pnl < 0:

            losses += 1

        if pnl > best_trade:

            best_trade = pnl

        if pnl < worst_trade:

            worst_trade = pnl

    total = len(completed)

    win_rate = (

        wins / total * 100

        if total > 0

        else 0

    )

    trade_stats["wins"] = wins

    trade_stats["losses"] = losses

    trade_stats["total_trades"] = total

    trade_stats["total_profit"] = round(

        total_profit,

        2

    )

    trade_stats["win_rate"] = round(

        win_rate,

        2

    )

    trade_stats["best_trade"] = round(

        best_trade,

        2

    )

    trade_stats["worst_trade"] = round(

        worst_trade,

        2

    )

    if coin_pnl:

        trade_stats["best_coin"] = max(

            coin_pnl,

            key=coin_pnl.get

        )

        trade_stats["worst_coin"] = min(

            coin_pnl,

            key=coin_pnl.get

        )


# ============================================================
# PORTFOLIO METRICS
# ============================================================

def portfolio_metrics():

    equity = [

        x.get(

            "portfolio",

            0

        )

        for x

        in storage.portfolio_history

    ]

    if not equity:

        return {

            "current_drawdown": 0,

            "max_drawdown": 0

        }

    peak = equity[0]

    max_dd = 0

    for x in equity:

        peak = max(

            peak,

            x

        )

        dd = (

            (peak - x)

            / peak

        ) * 100 if peak else 0

        max_dd = max(

            max_dd,

            dd

        )

    current_dd = (

        (peak - equity[-1])

        / peak

    ) * 100 if peak else 0

    return {

        "current_drawdown":

            round(

                current_dd,

                2

            ),

        "max_drawdown":

            round(

                max_dd,

                2

            )

    }


# ============================================================
# EQUITY CURVE
# ============================================================

def equity_curve():

    return [

        x.get(

            "portfolio",

            0

        )

        for x

        in storage.portfolio_history

    ]


# ============================================================
# LOG TRADE
# ============================================================

def log_trade(

    coin,

    action,

    price,

    amount,

    pnl=0

):

    entry = {

        "time":

            datetime.now()

            .strftime(

                "%Y-%m-%d %H:%M:%S"

            ),

        "coin": coin,

        "action": action,

        "price": round(

            price,

            2

        ),

        "amount": round(

            amount,

            2

        ),

        "pnl": round(

            pnl,

            2

        )

    }

    storage.trade_log.append(

        entry

    )

    update_stats()

    storage.save_data()


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def stats_cmd(

    update,

    context

):

    update_stats()

    msg = (

        f"📊 PHASE 5 STATS\n\n"

        f"Trades: "

        f"{trade_stats['total_trades']}\n"

        f"Wins: "

        f"{trade_stats['wins']}\n"

        f"Losses: "

        f"{trade_stats['losses']}\n"

        f"Win Rate: "

        f"{trade_stats['win_rate']}%\n"

        f"Total PnL: "

        f"₹{trade_stats['total_profit']}"

    )

    await update.message.reply_text(msg)


async def history_cmd(

    update,

    context

):

    if not storage.trade_log:

        await update.message.reply_text(

            "No Trade History"

        )

        return

    text = "📜 TRADE HISTORY\n\n"

    for t in storage.trade_log[-10:]:

        text += (

            f"{t['coin']} "

            f"{t['action']} "

            f"₹{t['pnl']}\n"

        )

    await update.message.reply_text(

        text

    )


async def analytics_cmd(

    update,

    context

):

    update_stats()

    dd = portfolio_metrics()

    msg = (

        "📈 ANALYTICS\n\n"

        f"Best Coin: "

        f"{trade_stats['best_coin']}\n"

        f"Worst Coin: "

        f"{trade_stats['worst_coin']}\n"

        f"Best Trade: "

        f"₹{trade_stats['best_trade']}\n"

        f"Worst Trade: "

        f"₹{trade_stats['worst_trade']}\n"

        f"Max DD: "

        f"{dd['max_drawdown']}%"

    )

    await update.message.reply_text(

        msg

    )