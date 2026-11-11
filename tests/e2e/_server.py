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


def _health(url):
    try:
        return urllib.request.urlopen(url + "api/health", timeout=10).status == 200
    except Exception:
        return False


def seed_db(db_path, env=None):
    """Run tests/e2e/seed.py against db_path; returns the id dict it prints."""
    env = {**(env or os.environ), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "e2e" / "seed.py"), str(db_path)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, f"seed failed:\n{out.stdout}\n{out.stderr[-3000:]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


class App:
    def __init__(self, proc, logf, log_path, url, port, ids, db_path):
        self.proc, self.logf, self.log_path = proc, logf, log_path
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
                "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev")})
    if external:
        env.update({"ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost", "ACT_TRUSTED_JWKS_PATH": str(JWKS)})
    if demo:
        env["ACT_DEMO_MODE"] = "1"
    env.update(extra_env or {})
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
        app = App(proc, logf, log_path, url, p, ids, db_path)
        last = log_path.read_text()[-3000:]
        if not (url and url.endswith("/")):
            app.stop()
            if port is None and "in use" in last.lower():
                continue
            raise AssertionError(f"no APP_URL:\n{last}")
        # Prefer the printed APP_URL (Domino proxy prefix); fall back to localhost if the proxy does not serve it.
        local = f"http://localhost:{p}/"
        for _ in range(30):
            if _health(url):
                break
            time.sleep(1)
        else:
            app.url = url = local
            for _ in range(60):
                if _health(local):
                    break
                time.sleep(1)
        if proc.poll() is not None:
            app.stop()
            if port is None:
                continue
            raise AssertionError(f"app exited:\n{last}")
        app.url = url
        return app
    raise AssertionError(f"app failed to start:\n{last}")
