"""Pure-ASGI middleware: serve requests that arrive under the Domino proxy prefix.

Domino forwards with the full URL prefix kept: DOMINO_RUN_HOST_PATH
(published App) or
/apps/<id>/ and /preview/<id>/ for Domino Apps, or DOMINO_RUN_HOST_PATH + "proxy/<port>/" (workspace App Preview).
The prefix is stripped so the app sees the same path as an unprefixed request.
"""
import re
from urllib.parse import quote


class ProxyPrefixMiddleware:
    def __init__(self, app, host_path: str = ""):
        self.app = app
        base = (host_path or "").strip().rstrip("/")
        self.patterns = [re.compile(rf"^({re.escape(base)}(?:/proxy/\d+)?)(/.*)?$")] if base else []
        # Domino App prefixes, accepted even when DOMINO_RUN_HOST_PATH doesn't name them:
        # workspace App Preview (/preview/<draftAppId>) and published Apps (/apps/<app-id>).
        self.patterns.append(re.compile(r"^(/(?:preview|apps)/[A-Za-z0-9_-]+)(/.*)?$"))

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        m = next((m for m in (p.match(scope["path"]) for p in self.patterns) if m), None)
        if not m:
            return await self.app(scope, receive, send)
        prefix, rest = m.group(1), m.group(2)
        if rest is None:  # bare prefix: relative asset URLs need the trailing slash
            if scope["type"] == "http":
                loc = quote(scope["path"], safe="/%:@") + "/"
                if scope.get("query_string"):
                    loc += "?" + scope["query_string"].decode("latin-1")
                await send({"type": "http.response.start", "status": 307,
                            "headers": [(b"location", loc.encode("latin-1")), (b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
            rest = "/"
        scope = dict(scope, path=rest)
        if scope.get("raw_path"):
            scope["raw_path"] = rest.encode("latin-1", "ignore")
        await self.app(scope, receive, send)
