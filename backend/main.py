"""ACT FastAPI app: API + built frontend."""
import importlib
import os
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, llm, routes
from .prefix import ProxyPrefixMiddleware
from .domino_identity import get_user

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app):
    llm.warn_if_mock()
    db.init_db()
    db.prewarm(int(os.environ.get("ACT_DB_PREWARM_COUNT", "10")))
    yield


app = FastAPI(title="Agent Control Tower", lifespan=lifespan)


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def write_guard(request, call_next):
    """Reject unauthenticated writes on every path (incl. /v1/*) before routing."""
    if request.method in WRITE_METHODS and await run_in_threadpool(get_user, request) is None:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok",
            "llm_provider": "mock" if (os.environ.get("ACT_LLM_PROVIDER") or "gateway").strip().lower() == "mock" else "gateway"}


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
    return "notebookSession" in os.environ.get("DOMINO_RUN_HOST_PATH", "")


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
