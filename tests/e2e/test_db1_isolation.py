"""DB-1 / DB1-2: every app the e2e harness starts has its own tmp ACT_DB and ACT_DEMO_DB_PATH, never /mnt/data/."""
import json
import urllib.request

from _server import wait_until

OWNER = {"X-Domino-User-Id": "owner-1", "X-Domino-User": "owner.user"}
ENV = {"DOMINO_PROJECT_OWNER": "owner.user", "ACT_WORKSPACE_OWNER_MODE": "0"}


def test_harness_apps_use_tmp_dbs(apps):
    started = [apps(seed=True), apps(external=False, extra_env=ENV), apps(demo=True)]
    for app in started:
        for name in ("ACT_DB", "ACT_DEMO_DB_PATH"):
            val = app.env.get(name)
            assert val, f"{name} not set for the app on port {app.port}"
            assert not val.startswith("/mnt/data/"), f"{name}={val} is under /mnt/data/"
    assert len({a.env["ACT_DEMO_DB_PATH"] for a in started}) == len(started), "demo DB paths must be per app"


def test_demo_on_creates_db_at_tmp_demo_path(apps):
    app = apps(external=False, extra_env=ENV)
    demo_path = app.demo_db_path
    assert demo_path and not str(demo_path).startswith("/mnt/data/")
    req = urllib.request.Request(app.url + "api/settings/demo_mode", method="PUT", headers={**OWNER, "Content-Type": "application/json"},
                                 data=json.dumps({"value": True}).encode())
    try:
        urllib.request.urlopen(req, timeout=60).read()
        wait_until(demo_path.exists, 15, 0.25, f"demo DB created at {demo_path}")
    finally:
        off = urllib.request.Request(app.url + "api/settings/demo_mode", method="PUT",
                                     headers={**OWNER, "Content-Type": "application/json"}, data=b'{"value": false}')
        try:
            urllib.request.urlopen(off, timeout=60).read()
        except Exception:
            pass
