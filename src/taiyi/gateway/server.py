"""Thin stdlib HTTP transport over GatewayApp.

http.server is deliberate: zero dependencies, fine for single-machine/MVP use.
A FastAPI/uvicorn transport can wrap the same GatewayApp later for scale; the app
logic does not change.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from taiyi.gateway.app import GatewayApp


def _make_handler(app: GatewayApp):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            path = self.path.split("?", 1)[0]
            status, data = app.handle(method, path, self.headers, body)
            if isinstance(data, str):  # e.g. Prometheus /metrics text
                payload = data.encode("utf-8")
                content_type = "text/plain; version=0.0.4"
            else:
                payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, *args) -> None:  # keep the test/CLI output clean
            pass

    return Handler


def make_server(app: GatewayApp, host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
    return HTTPServer((host, port), _make_handler(app))
