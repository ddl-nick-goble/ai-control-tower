#!/usr/bin/env python
"""Screenshot app pages for design review.

Starts the app on a free port (e2e seed by default, demo mode with --demo), optionally signs in, takes full-page PNGs
named <slug>-<width>.png, prints their paths, and stops the app.

Usage:
  python tests/e2e/capture.py --out DIR --widths 1440,1024 --demo \\
      --route '#/agents/<id>' --route '#/settings' \\
      [--delay-api 'api/settings:3000'] [--fail-api 'api/agents/*:500'] [--as owner|admin|NAME] [--no-auth]

Options:
  --out DIR         output directory (created)
  --widths W,W      viewport widths in px (default 1440); height 900, full_page capture
  --demo            start in demo mode (ACT_DEMO_MODE=1, empty DB) instead of the e2e seed
  --route R         route to capture (repeatable; default '#/'). '#/x' or '/x' hashes are accepted; the slug is derived
                    from it ('#/agents/abc' -> agents-abc, '#/' -> home). With the e2e seed, the tokens {recon_id},
                    {treasury_id}, {run_id} are replaced by the seeded ids.
  --as NAME         identity sent as X-Domino-User-Id / X-Domino-User (default 'owner'; 'admin' also works). The
                    same header injection the e2e tests use (context.set_extra_http_headers).
  --no-auth         send no identity headers (anonymous)
  --delay-api SPEC  'url-glob-substring:MILLIS' (repeatable): hold matching API responses for MILLIS ms (loading states;
                    keep --settle below MILLIS to capture the loading state).
                    Matched as a glob against the request URL, e.g. 'api/settings:3000' -> **/api/settings*
  --fail-api SPEC   'url-glob:STATUS' (repeatable): answer matching requests with that HTTP status (error states),
                    e.g. 'api/agents/*:500'
  --settle MS       extra wait after load before each shot (default 800)
"""
import argparse
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _server import start_app  # noqa: E402


def _slug(route):
    s = re.sub(r"[^A-Za-z0-9]+", "-", route.lstrip("#/")).strip("-")
    return s or "home"


def _spec(spec):
    pat, _, val = spec.rpartition(":")
    if not pat or not val.isdigit():
        raise SystemExit(f"bad spec {spec!r}: expected 'url-glob:NUMBER'")
    if "*" not in pat:
        pat = f"**/{pat.lstrip('/')}*"
    elif not pat.startswith(("**", "http")):
        pat = f"**/{pat.lstrip('/')}"
    return pat, int(val)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Screenshot app pages for design review.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--widths", default="1440")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--route", action="append")
    ap.add_argument("--as", dest="user", default="owner")
    ap.add_argument("--no-auth", action="store_true")
    ap.add_argument("--delay-api", action="append", default=[])
    ap.add_argument("--fail-api", action="append", default=[])
    ap.add_argument("--settle", type=int, default=800)
    a = ap.parse_args(argv)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in a.widths.split(",") if w.strip()]
    routes = a.route or ["#/"]
    delays = [_spec(s) for s in a.delay_api]
    fails = [_spec(s) for s in a.fail_api]

    from playwright.sync_api import sync_playwright

    paths = []
    with tempfile.TemporaryDirectory(prefix="act-capture-") as tmp:
        app = start_app(tmp, seed=not a.demo, demo=a.demo)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                for width in widths:
                    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=app.url)
                    if not a.no_auth:
                        ctx.set_extra_http_headers({"X-Domino-User-Id": a.user, "X-Domino-User": a.user})
                    page = ctx.new_page()
                    for pat, ms in delays:
                        def _delay(route, ms=ms):
                            resp = route.fetch()
                            page.wait_for_timeout(ms)  # keeps the event loop running while the response is held
                            route.fulfill(response=resp)
                        page.route(pat, _delay)
                    for pat, status in fails:
                        ctx.route(pat, lambda r, status=status: r.fulfill(
                            status=status, content_type="application/json", body='{"detail":"injected failure"}'))
                    for r in routes:
                        r = r.format(**{k: v for k, v in app.ids.items() if isinstance(v, str)}) if "{" in r else r
                        target = r if r.startswith("#") else "#" + r
                        page.goto("./" + target)
                        page.wait_for_load_state("load")
                        page.wait_for_timeout(a.settle)
                        page.evaluate("window.scrollTo(0, 0)")
                        path = out / f"{_slug(r)}-{width}.png"
                        page.screenshot(path=str(path), full_page=True)
                        paths.append(str(path.resolve()))
                        print(paths[-1], flush=True)
                    ctx.close()
                browser.close()
        finally:
            app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
