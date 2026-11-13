"""Shared e2e app launcher: start app.sh on a free OS-assigned port with its own temp ACT_DB; stop by process group.

Never kills anything by port. Ports 9888-9895 (used by older suites that may run in the same workspace) are never picked.
"""
import fcntl
import json
import os
import pathlib
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
JWKS = ROOT / "fixtures" / "external" / "signing-key.pub.jwk.json"
RESERVED = range(9888, 9896)
_LOCK = pathlib.Path(tempfile.gettempdir()) / "act-e2e-start.lock"


def free_port():
    """Ask the OS for a free port (bind port 0), skipping the legacy 9888-9895 range."""
    for _ in range(50):
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        if port not in RESERVED:
            return port
    raise RuntimeError("no free port")


def _health(url, timeout=10):
    try:
        return urllib.request.urlopen(url + "api/health", timeout=timeout).status == 200
    except Exception:
        return False


def copy_db(src, dst):
    """Consistent copy of a SQLite DB through the backup API; also safe onto a DB another process has open."""
    s, d = sqlite3.connect(str(src)), sqlite3.connect(str(dst), timeout=30)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


_SEEDED = {}  # seed once per run: {"db": template path, "ids": ids}


def seed_db(db_path, env=None):
    """Seed db_path with tests/e2e/seed.py; returns the id dict it prints. The seed runs once per process: later calls
    copy the seeded DB."""
    if _SEEDED:
        copy_db(_SEEDED["db"], db_path)
        return dict(_SEEDED["ids"])
    ids = _run_seed(db_path, env)
    tpl = pathlib.Path(tempfile.mkdtemp(prefix="act-e2e-seed-")) / "seeded.db"
    copy_db(db_path, tpl)
    _SEEDED.update(db=tpl, ids=ids)
    return dict(ids)


def _run_seed(db_path, env=None):
    env = {**(env or os.environ), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "e2e" / "seed.py"), str(db_path)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, f"seed failed:\n{out.stdout}\n{out.stderr[-3000:]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


class App:
    def __init__(self, proc, logf, log_path, url, port, ids, db_path, env=None):
        self.proc, self.logf, self.log_path = proc, logf, log_path
        self.env = dict(env or {})
        self.demo_db_path = pathlib.Path(self.env["ACT_DEMO_DB_PATH"]) if self.env.get("ACT_DEMO_DB_PATH") else None
        self.url, self.port, self.ids, self.db_path = url, port, ids, db_path

    def stop(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.logf.close()


def start_app(tmp, *, seed=False, demo=False, external=True, port=None, extra_env=None, strip_env=(), timeout=300):
    """Start app.sh on a free port (or `port`, e.g. to restart on the same URL) and wait until it is healthy."""
    tmp = pathlib.Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    # Inherited ACT_* / BREAK_CLASSIFIER_* (and any names in strip_env) never leak in; tests set them via extra_env.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ACT_", "BREAK_CLASSIFIER_")) and k not in set(strip_env)}
    db_path = tmp / "act.db"
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "HOST": "0.0.0.0", "ACT_DB": str(db_path),
                "ACT_DEMO_DB_PATH": str(tmp / "demo.db"),
                "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"),
                # DB-1: test apps never track to the workspace MLflow proxy (demo seeding uploads many runs/traces to it,
                # ~1s+ each, which times out the demo-switch tests and pollutes the real experiment); a tmp file store.
                "MLFLOW_TRACKING_URI": f"file://{tmp / 'mlruns'}"})
    if external:
        env.update({"ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost", "ACT_TRUSTED_JWKS_PATH": str(JWKS)})
    if demo:
        env["ACT_DEMO_MODE"] = "1"
    env.update(extra_env or {})
    # DB-1: never the live /mnt/data DBs, whatever a test passes in extra_env
    env["ACT_DB"] = env["ACT_DB"] if not str(env["ACT_DB"]).startswith("/mnt/data/") else str(db_path)
    env["ACT_DEMO_DB_PATH"] = (env["ACT_DEMO_DB_PATH"] if not str(env["ACT_DEMO_DB_PATH"]).startswith("/mnt/data/")
                               else str(tmp / "demo.db"))
    ids = seed_db(db_path, env) if seed else {}

    log_path = tmp / "app.log"
    last = ""
    for attempt in range(3):
        p = port or free_port()
        env["PORT"] = str(p)
        logf = open(log_path, "w")
        # serialize startup: app.sh may (re)build the frontend, which must not run twice concurrently
        with open(_LOCK, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            proc = subprocess.Popen(["bash", "app.sh"], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, env=env,
                                    start_new_session=True)
            url, deadline = None, time.time() + timeout
            while time.time() < deadline and url is None and proc.poll() is None:
                m = re.search(r"APP_URL=(\S+)", log_path.read_text())
                if m:
                    url = m.group(1)
                else:
                    time.sleep(0.3)
        app = App(proc, logf, log_path, url, p, ids, db_path, env)
        last = log_path.read_text()[-3000:]
        if not (url and url.endswith("/")):
            app.stop()
            if port is None and "in use" in last.lower():
                continue
            raise AssertionError(f"no APP_URL:\n{last}")
        # Wait on localhost (fast, always reachable), then prefer the printed APP_URL (Domino proxy prefix) if the proxy
        # serves it right now; one probe, so a proxy that is not reachable from here costs nothing.
        local = f"http://localhost:{p}/"
        try:
            wait_until(lambda: _health(local) or proc.poll() is not None, 120, 0.3, f"{local} healthy")
        except AssertionError:
            app.stop()
            raise
        if proc.poll() is None and not _health(url, timeout=3):
            if os.environ.get("E2E_REQUIRE_PROXY") == "1":
                app.stop()
                raise AssertionError(f"E2E_REQUIRE_PROXY=1 but the proxy URL {url} is not reachable")
            print(f"[e2e] proxy URL {url} not reachable; falling back to {local}", file=sys.stderr, flush=True)
            url = local
        if proc.poll() is not None:
            app.stop()
            if port is None:
                continue
            raise AssertionError(f"app exited:\n{last}")
        app.url = url
        return app
    raise AssertionError(f"app failed to start:\n{last}")


def wait_until(cond, timeout=10.0, interval=0.25, what="condition"):
    """Documented polling helper: call cond() until it returns truthy or `timeout` seconds pass."""
    end = time.time() + timeout
    while True:
        v = cond()
        if v:
            return v
        if time.time() >= end:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(interval)


def wait_port(port, host="127.0.0.1", timeout=10.0):
    """Wait until something accepts TCP connections on host:port."""
    def up():
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return True
        except OSError:
            return False
    wait_until(up, timeout, 0.25, f"port {port}")


class AppRegistry:
    """One running app per distinct environment, started lazily and cached by an env key for the whole run.

    The first get() for a key starts the app (the seeded DB is copied from the one seed) and snapshots its DB.
    reset() (called at each module start) restores every snapshot over the live DB, so modules that share an
    environment still begin from a fresh DB without a restart. Demo apps are not restored (their tests reset the
    state through the demo API).
    """

    def __init__(self, base_tmp):
        self.base_tmp = pathlib.Path(base_tmp)
        self.apps, self.snap, self.starts = {}, {}, []

    @staticmethod
    def key(seed=False, demo=False, external=True, extra_env=None, strip_env=()):
        return (bool(seed), bool(demo), bool(external), tuple(sorted((extra_env or {}).items())),
                tuple(sorted(set(strip_env))))

    def get(self, *, seed=False, demo=False, external=True, extra_env=None, strip_env=()):
        key = self.key(seed, demo, external, extra_env, strip_env)
        app = self.apps.get(key)
        if app is None:
            tmp = self.base_tmp / f"app{len(self.apps)}"
            app = start_app(tmp, seed=seed, demo=demo, external=external, extra_env=extra_env, strip_env=strip_env)
            self.apps[key] = app
            self.starts.append(key)
            if app.db_path.exists():
                self.snap[key] = tmp / "snapshot.db"
                copy_db(app.db_path, self.snap[key])
        return app

    def reset(self):
        for key, snap in self.snap.items():
            copy_db(snap, self.apps[key].db_path)

    def stop_all(self):
        for app in self.apps.values():
            app.stop()
        self.apps.clear()
