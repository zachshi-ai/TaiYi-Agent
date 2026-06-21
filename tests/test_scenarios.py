"""Scenario registry + matcher (Module 8)."""
from __future__ import annotations

from taiyi.scenarios import ScenarioMatcher, ScenarioRegistry


def test_catalog_loads():
    reg = ScenarioRegistry.load_dir()
    assert {"dev.git", "ops.report", "customer_service.refund"} <= set(reg.names())
    assert reg.get("dev.git").triggers


def test_matcher_selects_by_triggers():
    matcher = ScenarioMatcher(ScenarioRegistry.load_dir())
    assert matcher.match("please commit my code with git") == "dev.git"
    assert matcher.match("生成上周周报") == "ops.report"
    assert matcher.match("处理一个退款") == "customer_service.refund"


def test_matcher_falls_back_to_default():
    matcher = ScenarioMatcher(ScenarioRegistry.load_dir(), default="default")
    assert matcher.match("tell me a story about the sea") == "default"
