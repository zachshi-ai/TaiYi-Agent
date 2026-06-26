"""Scenario matching and the Skill quality gate. Run from the repo root:

    python3 examples/skills_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.scenarios import ScenarioMatcher, ScenarioRegistry  # noqa: E402
from taiyi.skills import SkillRegistry  # noqa: E402
from taiyi.skills.loader import load_skill  # noqa: E402


def main() -> None:
    print("== Scenario matching ==")
    matcher = ScenarioMatcher(ScenarioRegistry.load_dir())
    for prompt in ("commit my code", "生成上周周报", "处理一个退款", "tell me a joke"):
        print(f"  {prompt!r:24s} -> {matcher.match(prompt)}")

    print("\n== Shipped skill catalog (all gated) ==")
    reg = SkillRegistry.load_dir()
    for s in reg.all():
        flag = "production" if s.production_eligible else "SANDBOX"
        print(f"  [{flag:10s}] {s.name:16s} risk={s.risk}")

    print("\n== A skill with no quality gate is refused from production ==")
    with tempfile.TemporaryDirectory() as d:
        sk = Path(d) / "ungated"
        sk.mkdir()
        (sk / "SKILL.md").write_text("---\nname: ungated\ncategory: managed\n---\n# x\n", encoding="utf-8")
        skill = load_skill(sk)
        print(f"  ungated.production_eligible = {skill.production_eligible}  ({skill.gate_problems})")


if __name__ == "__main__":
    main()
