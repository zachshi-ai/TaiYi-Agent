"""macOS .app entry point for Taiyi.

Double-clicking the app runs this: it starts the Taiyi gateway on localhost and
opens the bundled web UI in the default browser. The app bundle is read-only, so
all persistence goes to a writable location under
``~/Library/Application Support/Taiyi``. On first launch a ``taiyi.yaml`` is
written there (offline defaults); edit it (or use the in-UI Config panel) and
relaunch to point at a real model.

This launcher is also runnable un-frozen for testing:
    PYTHONPATH=src python3 deploy/macos/taiyi_launcher.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _resource_base() -> Path:
    """Where bundled data lives: sys._MEIPASS when frozen, else the repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # deploy/macos/taiyi_launcher.py → repo root is two levels up.
    return Path(__file__).resolve().parents[2]


def _support_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "Taiyi"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    base = _resource_base()
    # When frozen, the package modules are unpacked under sys._MEIPASS; make sure
    # `src` is importable when running un-frozen for testing.
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(base / "src"))

    from taiyi.config import TaiyiConfig, load_config
    from taiyi.gateway import AuthPolicy, GatewayApp, build_gateway_from_config
    from taiyi.gateway.server import make_server

    support = _support_dir()
    cfg_path = support / "taiyi.yaml"

    if cfg_path.exists():
        cfg = load_config(cfg_path)
    else:
        # First launch: write an offline-default config the user can later edit.
        cfg = TaiyiConfig(base_dir=str(support), config_path=str(cfg_path))
        try:
            from taiyi.cli import _render_config

            defaults = {
                "base_dir": str(support), "host": "127.0.0.1", "port": 8080,
                "auth_tokens": "[]", "executor": "mock",
                "sandbox_dir": str(support / "sandbox"), "sandbox_backend": "local",
                "max_rounds": 1, "mode": "agent", "provider": "offline",
                "base_url": None, "model": None, "api_key": None,
                "api_key_env": None, "log_level": "info",
            }
            cfg_path.write_text(_render_config(defaults), encoding="utf-8")
        except Exception:
            pass  # config file is a convenience; defaults still run

    # base_dir must be writable — never inside the read-only .app bundle.
    if not cfg.base_dir:
        cfg.base_dir = str(support)

    gw = build_gateway_from_config(cfg)
    app = GatewayApp(gw, auth=AuthPolicy(tuple(cfg.auth_tokens)))
    app.config_path = str(cfg_path)

    static_dir = str(base / "web" / "dist")
    if not Path(static_dir).is_dir():
        static_dir = None

    host = cfg.host or "127.0.0.1"
    port = int(cfg.port or 8080)
    url = f"http://{host}:{port}/"

    try:
        server = make_server(app, host=host, port=port, static_dir=static_dir)
    except OSError:
        # Port already in use — assume a Taiyi is already running and just open it.
        webbrowser.open(url)
        return 0

    def _open_when_up():
        time.sleep(1.2)
        webbrowser.open(url)

    threading.Thread(target=_open_when_up, daemon=True).start()
    print(f"Taiyi listening on {url}  (data in {support})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
