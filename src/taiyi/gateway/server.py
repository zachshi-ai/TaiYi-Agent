"""Thin stdlib HTTP transport over GatewayApp.

http.server is deliberate: zero dependencies, fine for single-machine/MVP use.
A FastAPI/uvicorn transport can wrap the same GatewayApp later for scale; the app
logic does not change.

Static file serving (for the bundled web UI) is handled HERE, in the transport
layer, before any request reaches ``app.handle``. This keeps ``app.handle``'s
``(status, dict|str)`` contract purely about the JSON API — static bytes never
flow through it. The web UI's build output (``web/dist``) is served same-origin,
so no CORS is needed.
"""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from taiyi.gateway.app import GatewayApp

# API paths are always handled by app.handle; only non-API GETs may be static.
_API_PREFIXES = ("/v1/", "/healthz", "/metrics")


def _guess_content_type(path: Path) -> str:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or "application/octet-stream"


def _make_handler(app: GatewayApp, static_dir: Path | None = None):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            # Static files: only GET, only when a static_dir is configured, and
            # only for paths that are NOT API routes. This branch returns bytes
            # with a real Content-Type and never calls app.handle.
            if method == "GET" and static_dir is not None:
                raw = self.path.split("?", 1)[0]
                if not raw.startswith(_API_PREFIXES):
                    served = self._serve_static(raw)
                    if served:
                        return

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            path = self.path.split("?", 1)[0]
            try:
                status, data = app.handle(method, path, self.headers, body)
            except Exception as exc:  # noqa: BLE001
                # Never let a handler exception drop the connection with no
                # response — the browser would show a bare "Failed to fetch"
                # with no clue. Return a 500 with the error instead.
                status = 500
                data = {"error": f"{type(exc).__name__}: {exc}"}
            if isinstance(data, str):  # e.g. Prometheus /metrics text
                payload = data.encode("utf-8")
                content_type = "text/plain; version=0.0.4"
            else:
                payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
            self._respond(status, payload, content_type)

        def _respond(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _serve_static(self, raw_path: str) -> bool:
            """Serve a file from static_dir. SPA fallback: unknown paths return
            index.html so client-side routing works. Returns True if served."""
            rel = raw_path.lstrip("/")
            # Prevent path traversal: resolve and verify it stays under static_dir.
            target = (static_dir / rel).resolve() if rel else static_dir / "index.html"
            try:
                target.relative_to(static_dir.resolve())
            except ValueError:
                self._respond(403, b'{"error":"forbidden"}', "application/json")
                return True
            if target.is_file():
                payload = target.read_bytes()
                self._respond(200, payload, _guess_content_type(target))
                return True
            # SPA fallback: any non-file, non-API path serves the app shell so
            # the React router can take over (e.g. /approvals, /ooda).
            index = static_dir / "index.html"
            if index.is_file():
                payload = index.read_bytes()
                self._respond(200, payload, "text/html; charset=utf-8")
                return True
            return False

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_OPTIONS(self) -> None:
            # Same-origin UI needs no CORS, but answer OPTIONS gracefully anyway.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args) -> None:  # keep the test/CLI output clean
            pass

    return Handler


def make_server(
    app: GatewayApp,
    host: str = "127.0.0.1",
    port: int = 8080,
    static_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    sd = Path(static_dir).resolve() if static_dir else None
    # Threaded so one slow request (e.g. a live-LLM connection test that blocks
    # on the network) never freezes the whole gateway — which would make every
    # other request, including a config write-back, fail with "Failed to fetch".
    # The SQLite memory/iteration layers already open with check_same_thread=False.
    return ThreadingHTTPServer((host, port), _make_handler(app, sd))
