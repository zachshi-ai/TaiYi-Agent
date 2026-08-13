"""Tamper-evident audit log."""
from __future__ import annotations

import multiprocessing

from taiyi.core.audit import AuditLog


def _append_from_process(path: str, worker: int) -> None:
    AuditLog(path).append("worker_event", worker=worker)


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


def test_stale_audit_instance_reloads_latest_chain_head_before_append(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    stale = AuditLog(path)

    first.append("first_owner")
    stale.append("later_owner")

    reloaded = AuditLog(path)
    assert [record.event for record in reloaded.records] == [
        "first_owner",
        "later_owner",
    ]
    assert reloaded.verify() == (True, None)


def test_concurrent_processes_preserve_one_audit_hash_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_from_process, args=(str(path), worker))
        for worker in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0] * len(processes)
    reloaded = AuditLog(path)
    assert len(reloaded.records) == len(processes)
    assert sorted(record.payload["worker"] for record in reloaded.records) == list(range(8))
    assert reloaded.verify() == (True, None)
