import json
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bots.scanner_bot.scanner import get_signals

from bots.scanner_bot.scanner import get_watchlist
from bots.scanner_bot.scanner import get_stats

app = FastAPI(title="PROJECT-A ULTIMATE DASHBOARD Framework")

app.mount(
    "/static",
    StaticFiles(directory="dashboard/static"),
    name="static"
)
templates = Jinja2Templates(directory="dashboard/templates")

def pull_state_payload():

    return {

        "service_statuses": {
            "scanner": "ONLINE",
            "trading_bot": "OFFLINE",
            "telegram_bot": "ONLINE"
        },

        "railway_monitoring": {
            "status": "ACTIVE"
        },

        "market_state": "ACTIVE",

        "recent_signals": [],

        "notifications": [],

        "error_logs": []
    }
@app.get("/", response_class=HTMLResponse)
async def viewport_router(request: Request):

    state = pull_state_payload()
    signals = get_signals()
    stats = get_stats()
    watchlist = get_watchlist()


    return templates.TemplateResponse(
        request=request,

        name="dashboard.html",
        context={
            "request": request,
            "data": state,
            "signals": signals,
            "stats": stats,
            "watchlist": watchlist
        }
    )
@app.get("/api/v1/state", response_class=JSONResponse)
async def unified_state_polling_endpoint():
    """Future production data hook. Live bots simply post metrics to rewrite state."""
    return pull_state_payload()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0",port=8080)
  
