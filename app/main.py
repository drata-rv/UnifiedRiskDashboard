"""
FastAPI entrypoint. Run locally with:
    uvicorn app.main:app --reload

In production, Azure Easy Auth (App Service or Container Apps built-in
authentication) sits in front of this app and injects X-MS-CLIENT-PRINCIPAL-*
headers — see user_auth.py. No auth/session code lives here.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routes import dashboard, export, history, registers, sync

app = FastAPI(title="FirstService Risk Dashboard")

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    """
    Lightweight CSRF defense: this app has no session/token machinery of its
    own (Easy Auth owns the login cookie), so state-changing requests are
    rejected unless their Origin matches the host actually being called.
    Browsers always send Origin on cross-origin POSTs; a same-origin form
    submit from our own pages matches, a forged cross-site form doesn't.
    Requests with no Origin header (non-browser callers, same-origin fetches
    in older browsers) are allowed through — this is defense-in-depth on top
    of Easy Auth, not a replacement for it.
    """
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin not in (f"https://{request.url.netloc}", f"http://{request.url.netloc}"):
            return PlainTextResponse("Cross-site request rejected", status_code=403)
    return await call_next(request)

app.include_router(dashboard.router)
app.include_router(registers.router)
app.include_router(sync.router)
app.include_router(history.router)
app.include_router(export.router)


@app.on_event("startup")
def on_startup():
    db.init_db()
