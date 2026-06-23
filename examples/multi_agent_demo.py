"""Multi-agent review: expert matrix + red-line veto + arbitration.

Replicates Design Doc scenario C (contract review). Run from the repo root:

    python3 examples/multi_agent_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.multi_agent import ExpertCommittee, reconsider_once  # noqa: E402


def show(title, result) -> None:
    print(f"== {title} ==")
    print(f"  decision: {result.decision.value}  escalate={result.escalate}  conflict={result.conflict}")
    if result.winning:
        print(f"  winning veto: {result.winning.domain} (precedence {result.winning.precedence}) — {result.winning.reason}")
    for a in result.advisories:
        print(f"  advisory: {a}")
    print(f"  notes: {result.notes}\n")


def main() -> None:
    committee = ExpertCommittee()

    show("Clean contract", committee.review("a straightforward contract with clear terms"))

    show(
        "Contract: data ownership undefined + confusing clause",
        committee.review("contract draft: data ownership undefined; clause 3 wording is confusing"),
    )

    print("== Reconsideration (amend the clause, review once more) ==")
    out = reconsider_once(
        committee,
        "contract draft: data ownership undefined; clause 3 wording is confusing",
        amend=lambda s: s.replace("data ownership undefined", "data ownership assigned to the client"),
    )
    print(f"  reconsidered={out.reconsidered} system_defect={out.system_defect}")
    print(f"  final decision: {out.result.decision.value}")
    for a in out.result.advisories:
        print(f"  advisory still noted: {a}")


if __name__ == "__main__":
    main()
