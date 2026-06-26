"""Run the founding scenarios through the *production* Governance Engine.

Unlike demo/ (the Phase 0 throwaway), this drives the real `taiyi` package and
the rules-as-data set. Run from the repo root:

    python3 examples/governance_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.core.types import PermitRequest  # noqa: E402
from taiyi.governance import GovernanceEngine  # noqa: E402

CASES = [
    ("Normal commit", "shell:git commit", ["-m", "fix tests"], "dev.git"),
    ("Identity override (founding incident)", "shell:git commit",
     ["-c", "user.name=OtherUser", "-m", "x"], "dev.git"),
    ("rm -rf /", "shell:rm -rf", ["/"], "default"),
    ("git push", "shell:git push", ["origin", "main"], "dev.git"),
    ("Report outbound notify", "notify:feishu", ["send", "ops-team", "report.pdf"], "ops.report"),
    ("Refund 200", "tool:refund", ["refund", "amount=200"], "customer_service.refund"),
    ("Refund 50", "tool:refund", ["refund", "amount=50"], "customer_service.refund"),
]


def main() -> None:
    engine = GovernanceEngine()
    print(f"Loaded {len(engine.rules)} rules\n")
    for title, tool, args, scenario in CASES:
        r = engine.issue_permit(
            PermitRequest(tool=tool, args=args, scenario=scenario, task_id="demo")
        )
        print(f"{title:42s} -> {r.verdict.value:13s} {r.matched_rule_id or ''}")
    ok, broken = engine.audit.verify()
    print(f"\nAudit: {len(engine.audit)} records, chain intact={ok}")


if __name__ == "__main__":
    main()
