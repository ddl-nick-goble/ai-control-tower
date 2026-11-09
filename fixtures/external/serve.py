#!/usr/bin/env python3
"""Serve the fixture A2A Agent Card at /.well-known/agent-card.json (stdlib only)."""
import argparse
import http.server
from pathlib import Path

CARD = Path(__file__).resolve().parent / "agent-card.json"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            body = CARD.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
