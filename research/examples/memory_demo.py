"""The 5-layer memory engine in action. Run from the repo root:

    python3 examples/memory_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.memory import MemoryEngine  # noqa: E402


def main() -> None:
    m = MemoryEngine()
    print(f"FTS5 backend available: {m.fts}\n")

    # L1 short-term
    m.add_message("s1", "user", "deploy the billing service")
    print(f"L1 session messages: {len(m.get_messages('s1'))}")

    # L5 + L3
    m.remember("the billing deploy failed because a migration timed out", tags=("incident",))
    m.remember("weekly report pipeline pulls from the analytics warehouse")
    print(f"L5 full-text 'migration': {[h.content[:40] for h in m.search_fulltext('migration')]}")
    sem = m.search_semantic("why did the billing deploy fail", top_k=1)
    print(f"L3 semantic top hit: {sem[0].content[:40]!r} (score={sem[0].score:.2f})")

    # L4 Honcho user model
    m.observe_user("prefers concise summaries")
    m.observe_user("dislikes emoji")
    print(f"L4 user model:\n  {m.get_user_model().replace(chr(10), chr(10)+'  ')}")

    # L2 skill index
    m.register_skill("git_safe_commit", "safe commits", tags=("git",))
    print(f"L2 skills: {m.list_skills()}")


if __name__ == "__main__":
    main()
