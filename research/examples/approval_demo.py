"""Human-in-the-loop: a suspended task is approved and resumed.

Run from the repo root:  python3 examples/approval_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.gateway import GatewayApp, build_gateway  # noqa: E402


def show(label, ctx_dict):
    tools = [s["tool"] + ("" if s["executed"] else " (held)") for s in ctx_dict["steps"]]
    print(f"  {label}: {ctx_dict['state']}  steps={tools}")


def main() -> None:
    app = GatewayApp(build_gateway())

    print("== Submit a weekly report (query allowed, outbound notify needs review) ==")
    _, task = app.handle("POST", "/v1/tasks", {}, '{"prompt":"帮我生成上周周报","scenario":"ops.report"}')
    show("submitted", task)
    approval_id = task["approval_id"]

    print("\n== Pending approvals ==")
    _, listing = app.handle("GET", "/v1/approvals", {}, "")
    for p in listing["pending"]:
        print(f"  {p['approval_id']}  tool={p['tool']}  reason={p['reason']}")

    print("\n== Human approves → the task resumes from where it stopped ==")
    import json
    _, resolved = app.handle("POST", "/v1/approvals/resolve", {},
                             json.dumps({"approval_id": approval_id, "decision": "approve"}))
    show("resumed", resolved)


if __name__ == "__main__":
    main()
