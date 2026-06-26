"""The Gateway: stack-wiring façade + HTTP-agnostic handlers. Run from repo root:

    python3 examples/gateway_demo.py

(The CLI is the other channel:  PYTHONPATH=src python3 -m taiyi.cli run "commit my changes")
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.gateway import AuthPolicy, GatewayApp, build_gateway  # noqa: E402


def main() -> None:
    app = GatewayApp(build_gateway(), auth=AuthPolicy(("secret",)))

    print("== POST /v1/tasks without a token ==")
    print("  ", app.handle("POST", "/v1/tasks", {}, '{"prompt":"commit my changes"}')[0], "(401)\n")

    hdr = {"Authorization": "Bearer secret"}
    print("== POST /v1/tasks (commit) ==")
    status, data = app.handle("POST", "/v1/tasks", hdr, '{"prompt":"commit my changes"}')
    print(f"  {status}  state={data['state']} scenario={data['scenario']} skill={data['skill']}\n")

    print("== POST /v1/tasks (refund 200) ==")
    status, data = app.handle("POST", "/v1/tasks", hdr, '{"prompt":"处理一个 200 元的退款"}')
    print(f"  {status}  state={data['state']} approval={data['approval_id']}\n")

    print("== POST /v1/chat/completions (OpenAI-compatible) ==")
    body = json.dumps({"model": "gpt-x", "messages": [{"role": "user", "content": "commit my changes"}]})
    status, data = app.handle("POST", "/v1/chat/completions", hdr, body)
    print(f"  {status}  content={data['choices'][0]['message']['content'].splitlines()[0]!r}")
    print(f"       taiyi={data['taiyi']}")


if __name__ == "__main__":
    main()
