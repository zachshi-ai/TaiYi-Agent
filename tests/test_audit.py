"""Tamper-evident audit log."""
from __future__ import annotations

from taiyi.core.audit import AuditLog


def test_chain_verifies_when_intact():
    log = AuditLog()
    for i in range(5):
        log.append("evt", i=i)
    ok, broken = log.verify()
    assert ok and broken is None


def test_tampering_with_payload_is_detected():
    log = AuditLog()
    log.append("evt", value="original")
    log.append("evt", value="second")
    log.append("evt", value="third")

    # Silently edit a past record's payload, leaving its stored hash in place.
    log.records[1].payload["value"] = "forged"

    ok, broken = log.verify()
    assert not ok
    assert broken == 1


def test_deleting_a_record_breaks_the_chain():
    log = AuditLog()
    log.append("evt", n=1)
    log.append("evt", n=2)
    log.append("evt", n=3)

    del log.records[1]  # remove the middle record

    ok, broken = log.verify()
    assert not ok


def test_persists_and_reloads_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("permit_decision", verdict="DENY")
    log.append("permit_decision", verdict="ALLOW")

    reloaded = AuditLog(path)
    assert len(reloaded) == 2
    ok, broken = reloaded.verify()
    assert ok and broken is None
    assert reloaded.records[0].payload["verdict"] == "DENY"
