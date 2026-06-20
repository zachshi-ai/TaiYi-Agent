"""L3.1 Governance — the neutral referee.

Loads rules-as-data (YAML), evaluates deterministic gates against execution
requests, and returns ALLOW / DENY / NEEDS_REVIEW. It never decides *what* to do;
it only decides whether a requested action may proceed.
"""

from taiyi.governance.rules import Rule, RuleError
from taiyi.governance.loader import load_rules, DEFAULT_RULES_DIR
from taiyi.governance.engine import GovernanceEngine

__all__ = [
    "Rule",
    "RuleError",
    "load_rules",
    "DEFAULT_RULES_DIR",
    "GovernanceEngine",
]
