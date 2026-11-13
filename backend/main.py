"""ACT FastAPI app: API + built frontend."""
import asyncio
import importlib
import logging
import os
import pkgutil
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, demo, llm, routes, settings
from .prefix import ProxyPrefixMiddleware
from .domino_identity import get_user, in_notebook_session

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"


class DbState:
    """Opening the DB can wait minutes for an NFS lock lease; that runs in a background thread so the App
    answers /api/health at once (Domino's App start must not block)."""

    def __init__(self):
        self.status = "starting"  # starting | ready | error
        self.error = None
        self.done = threading.Event()


def _open_db(app_, state):
    try:
        db.init_db()
        app_.state.demo_guard = None
        try:
            if demo.env_enabled():
                demo.warn_live_switches()
                demo.ensure_seeded()
            else:
                settings.apply_demo_setting()  # the switch saved from the Settings page
        except demo.DemoGuardError as e:
            app_.state.demo_guard = str(e)  # already logged; surfaced in /api/health and the banner
        db.prewarm(int(os.environ.get("ACT_DB_PREWARM_COUNT", "10")))
        state.status = "ready"
    except Exception as e:  # noqa: BLE001
        log.error("database could not be opened; the App stays up and reports it in /api/health: %s", e,
                  exc_info=True)
        state.error = str(e)
        state.status = "error"
    finally:
        state.done.set()


@asynccontextmanager
async def lifespan(_app):
    llm.warn_if_mock()
    _app.state.demo_guard = None
    state = _app.state.db_state = DbState()
    threading.Thread(target=_open_db, args=(_app, state), daemon=True, name="act-db-open").start()
    # A healthy DB opens in milliseconds: give it a short grace so the first requests see it ready.
    grace = float(os.environ.get("ACT_DB_STARTUP_GRACE", "5") or 5)
    await run_in_threadpool(state.done.wait, grace)
    try:
        yield
    finally:
        try:
            demo.switch_off()
        except demo.DemoGuardError:
            log.warning("demo switch-off at shutdown skipped: requests still running")
        demo.restore_defaults()


app = FastAPI(title="Agent Control Tower", lifespan=lifespan)


class DemoSwitchGate:
    """Pure ASGI gate: counts requests in flight and holds new ones while the runtime demo switch runs, so the
    switch can wait for in-flight requests to drain and none sees a half-seeded DB. /api/health is exempt."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].endswith("/api/health"):
            return await self.app(scope, receive, send)
        st = getattr(app.state, "db_state", None)
        if st is not None and st.status != "ready" and (scope["path"].startswith(("/api/", "/v1/"))):
            msg = ("Database is starting" if st.status == "starting"
                   else "Database could not be opened. See /api/health and the App logs.")
            resp = JSONResponse({"detail": msg}, status_code=503, headers={"Retry-After": "5"})
            return await resp(scope, receive, send)
        deadline = time.monotonic() + 30
        while not demo.enter():  # atomic: refuses while a switch runs, else counts this request
            if time.monotonic() > deadline:
                resp = JSONResponse({"detail": "The demo switch is still running. Try again in a moment."},
                                    status_code=503, headers={"Retry-After": "5"})
                return await resp(scope, receive, send)
            await asyncio.sleep(0.05)
        try:
            await self.app(scope, receive, send)
        finally:
            demo.leave()


app.add_middleware(DemoSwitchGate)


MAX_PREVIEW_BYTES = 8 * 1024
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def write_guard(request, call_next):
    """Reject unauthenticated writes on every path (incl. /v1/*) before routing."""
    if request.method == "POST" and request.url.path == "/api/tiering/preview":
        try:
            too_big = int(request.headers.get("content-length") or 0) > MAX_PREVIEW_BYTES
        except ValueError:
            too_big = True
        if too_big:
            return JSONResponse({"detail": "Request body too large (limit 8 KB)"}, status_code=413)
        return await call_next(request)  # read-only scoring; the only anonymous POST
    if (request.method in ("PUT", "DELETE") and re.fullmatch(r"/api/settings/[^/]+", request.url.path)
            and settings.settings_open()):
        return await call_next(request)  # SET-1: Settings writes are open; the route records "anonymous"
    if request.method in WRITE_METHODS and await run_in_threadpool(get_user, request) is None:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    st = getattr(app.state, "db_state", None)
    db_status = st.status if st else "ready"
    body = {"status": "ok" if db_status == "ready" else db_status, "db": db_status,
            "db_error": st.error if st and st.status == "error" else None, "demo_mode": demo.enabled(), "stand_ins": demo.stand_ins(),
            "llm_provider": demo.effective_llm_provider(),
            "demo_guard": getattr(app.state, "demo_guard", None) if demo.enabled() else None}
    return JSONResponse(body, status_code=503 if db_status == "error" else 200)


for _m in pkgutil.iter_modules(routes.__path__):
    _mod = importlib.import_module(f"{routes.__name__}.{_m.name}")
    if hasattr(_mod, "router"):
        app.include_router(_mod.router)

@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
               include_in_schema=False)
@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
               include_in_schema=False)
def _unmatched(rest: str):
    raise HTTPException(status_code=404, detail="Not found")


_MISSING_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Frontend build missing</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:3rem auto"><h1>Frontend build is missing</h1>
<p>The API is running (<code>api/health</code>), but <code>frontend/dist</code> was not found.</p>
<p>Build it with <code>npm install &amp;&amp; npm run build</code>, or run <code>ACT_REBUILD=1 bash app.sh</code>
in an environment with Node, then commit <code>frontend/dist</code>.</p></body></html>"""

if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    def _frontend_missing():
        return HTMLResponse(_MISSING_HTML, status_code=503)


# Outermost middleware: strip the Domino proxy prefix before routing and the write guard.
app.add_middleware(ProxyPrefixMiddleware, host_path=os.environ.get("DOMINO_RUN_HOST_PATH", ""))


def in_workspace() -> bool:
    return in_notebook_session()


def get_port() -> int:
    if os.environ.get("PORT"):
        return int(os.environ["PORT"])
    return 9888 if in_workspace() else 8888


def app_url(port: int) -> str:
    path = os.environ.get("DOMINO_RUN_HOST_PATH", "").rstrip("/")
    if in_workspace():
        path = f"{path}/proxy/{port}"
    host = os.environ.get("DOMINO_USER_HOST", "").rstrip("/")
    if not path and not host:
        return f"http://localhost:{port}/"
    return f"{host}{path}/"


if __name__ == "__main__":
    import uvicorn

    port = get_port()
    print(f"APP_URL={app_url(port)}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
