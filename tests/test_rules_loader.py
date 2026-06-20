"""Rule loader validation — fail fast on malformed rule sets."""
from __future__ import annotations

import pytest

from taiyi.governance import load_rules
from taiyi.governance.rules import RuleError


def test_default_rules_load_and_are_unique():
    rules = load_rules()
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    assert "authorship.git_identity.no_override" in ids


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return tmp_path


def test_missing_required_field_raises(tmp_path):
    _write(tmp_path, "bad.yaml", "id: x\ndomain: safety\n")
    with pytest.raises(RuleError):
        load_rules(tmp_path)


def test_unsupported_check_type_raises(tmp_path):
    _write(
        tmp_path,
        "judge.yaml",
        """
id: x.model_judge
domain: safety
severity: advisory
applies_to: ["shell:*"]
trigger: pre_execution
check:
  type: model_judge
on_fail:
  action: warn
  message: nope
precedence: 10
owner: someone
""",
    )
    with pytest.raises(RuleError):
        load_rules(tmp_path)


def test_duplicate_id_raises(tmp_path):
    rule = """
id: dup.rule
domain: safety
severity: red_line
applies_to: ["shell:rm*"]
trigger: pre_execution
check:
  type: deterministic
  match: tool_only
on_fail:
  action: block
  message: no
precedence: 10
owner: someone
"""
    _write(tmp_path, "a.yaml", rule)
    (tmp_path / "b.yaml").write_text(rule, encoding="utf-8")
    with pytest.raises(RuleError):
        load_rules(tmp_path)
