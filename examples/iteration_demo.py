"""Iteration / OODA (L5): the closed loop. Run from the repo root:

    python3 examples/iteration_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.core.types import PermitRequest  # noqa: E402
from taiyi.governance import GovernanceEngine  # noqa: E402
from taiyi.iteration import (  # noqa: E402
    IterationEngine,
    TaskRecord,
    approve,
    generate_skill_draft,
    suggest_rules,
    write_draft,
)
from taiyi.skills.loader import load_skill  # noqa: E402


def main() -> None:
    eng = IterationEngine()

    # Simulate recurring failures with a risky tool, and repeated novel work.
    for i in range(3):
        eng.store.add(TaskRecord(f"f{i}", "ops.x", None, "FAILED", "risky", ("tool:risky",), "boom"))
    for i in range(3):
        eng.store.add(TaskRecord(f"s{i}", "research.x", None, "COMPLETED", "dig", ("http:get", "file:write")))

    print("== Report ==")
    for k, v in eng.report().items():
        print(f"  {k}: {v}")

    print("\n== A recurring failure becomes a permanent check ==")
    with tempfile.TemporaryDirectory() as rules_dir:
        suggestion = suggest_rules(eng.store, threshold=3)[0]
        path = approve(suggestion, rules_dir)
        print(f"  approved rule -> {Path(path).name}")
        gov = GovernanceEngine(rules_dir=rules_dir)
        v = gov.issue_permit(PermitRequest(tool="tool:risky", scenario="ops.x", task_id="t")).verdict
        print(f"  governance now returns: {v.value} for tool:risky in ops.x")

    print("\n== Repeated work sediments into a gated skill ==")
    with tempfile.TemporaryDirectory() as skills_dir:
        draft = eng.propose_skills(min_repeats=3)[0]
        skill = load_skill(write_draft(draft, skills_dir))
        print(f"  generated {skill.name!r}  category={skill.category}  "
              f"production_eligible={skill.production_eligible}")


if __name__ == "__main__":
    main()
