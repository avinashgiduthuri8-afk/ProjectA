import json
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="PROJECT-A ULTIMATE DASHBOARD Framework")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def pull_state_payload():
    data_path = os.path.join("data", "dummy_data.json")
    try:
        with open(data_path, "r") as src:
            return json.load(src)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"error": "Critical State Configuration Matrix File Unreadable or Missing."}

@app.get("/", response_class=HTMLResponse)
async def viewport_router(request: Request):

    state = pull_state_payload()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "data": state
        }
    )
@app.get("/api/v1/state", response_class=JSONResponse)
async def unified_state_polling_endpoint():
    """Future production data hook. Live bots simply post metrics to rewrite state."""
    return pull_state_payload()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0",port=8080)
  
