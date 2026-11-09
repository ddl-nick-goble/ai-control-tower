"""ACT FastAPI app: API + built frontend."""
import importlib
import os
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db, routes

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    db.prewarm(int(os.environ.get("ACT_DB_PREWARM_COUNT", "10")))
    yield


app = FastAPI(title="Agent Control Tower", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}


for _m in pkgutil.iter_modules(routes.__path__):
    _mod = importlib.import_module(f"{routes.__name__}.{_m.name}")
    if hasattr(_mod, "router"):
        app.include_router(_mod.router)

if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")


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
