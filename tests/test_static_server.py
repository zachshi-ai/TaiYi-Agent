"""Pillar A1: static file serving + SPA fallback in the transport layer.

The server serves the built web UI same-origin. API paths still go to
GatewayApp.handle; non-API GETs resolve to files under static_dir, with
index.html fallback for client-side routing. Path traversal is blocked.
"""
from __future__ import annotations

import socket
import threading
import time
import urllib.request
from http.client import HTTPConnection
from pathlib import Path

from taiyi.gateway import GatewayApp, build_gateway
from taiyi.gateway.server import make_server


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(tmp_path, static_dir):
    app = GatewayApp(build_gateway(base_dir=tmp_path))
    port = _free_port()
    server = make_server(app, host="127.0.0.1", port=port, static_dir=static_dir)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def _get(port, path) -> tuple[int, str, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, body, ctype


def test_serves_index_html_at_root(tmp_path):
    sd = tmp_path / "web"
    sd.mkdir()
    (sd / "index.html").write_text("<html>taiyi ui</html>")
    for port in _serve(tmp_path, sd):
        status, body, ctype = _get(port, "/")
        assert status == 200
        assert "taiyi ui" in body
        assert "text/html" in ctype


def test_spa_fallback_for_unknown_path(tmp_path):
    sd = tmp_path / "web"
    sd.mkdir()
    (sd / "index.html").write_text("<html>shell</html>")
    for port in _serve(tmp_path, sd):
        # /approvals is a client-side route with no file — must fall back to index.html.
        status, body, _ = _get(port, "/approvals")
        assert status == 200
        assert "shell" in body


def test_serves_static_asset_with_content_type(tmp_path):
    sd = tmp_path / "web"
    sd.mkdir()
    (sd / "index.html").write_text("<html></html>")
    (sd / "app.js").write_text("console.log('hi')")
    for port in _serve(tmp_path, sd):
        status, body, ctype = _get(port, "/app.js")
        assert status == 200
        assert "console.log" in body
        assert "javascript" in ctype


def test_api_paths_still_go_to_app(tmp_path):
    sd = tmp_path / "web"
    sd.mkdir()
    (sd / "index.html").write_text("<html></html>")
    for port in _serve(tmp_path, sd):
        status, body, ctype = _get(port, "/healthz")
        assert status == 200
        assert "ok" in body
        assert "application/json" in ctype


def test_path_traversal_blocked(tmp_path):
    sd = tmp_path / "web"
    sd.mkdir()
    (sd / "index.html").write_text("<html></html>")
    secret = tmp_path / "secret.txt"
    secret.write_text("topsecret")
    for port in _serve(tmp_path, sd):
        status, body, _ = _get(port, "/../secret.txt")
        # Either 403 (traversal caught) or the normalized path resolves to a
        # non-file → SPA fallback returns index.html. The key invariant: the
        # secret content must never be served.
        assert "topsecret" not in body


def test_no_static_dir_means_api_only(tmp_path):
    # When static_dir is None, non-API GETs fall through to app.handle → 404.
    app = GatewayApp(build_gateway(base_dir=tmp_path))
    status, data = app.handle("GET", "/some/random/path", {}, "")
    assert status == 404
