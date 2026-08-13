"""Combined large-repository, context, and provider fault matrix.

The CI fixture is deterministic and dependency-free.  A caller may instead
point the same protocol at a pinned real checkout; the report records Git
origin, HEAD, inventory, and immutable TaiYi snapshot identity without storing
the checkout path or repository content in benchmark artifacts.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from taiyi.benchmark.schema import (
    LARGE_REPO_MANIFEST_SCHEMA,
    LARGE_REPO_RECEIPT_SCHEMA,
    LARGE_REPO_REPORT_SCHEMA,
    canonical_digest,
    write_artifact,
)
from taiyi.context import ContextEngine, RepositoryContextIndex
from taiyi.gateway import build_gateway
from taiyi.llm import LLMErrorKind, LLMRequestError, LLMResponse, ToolCall
from taiyi.runtime import RunPhase, TaskState
from taiyi.tools import SandboxExecutor

OPERATING_MODES = ("quality", "balanced", "efficiency")
MEASUREMENT_SCOPE = "taiyi_large_repository_combined_resilience"
FIXTURE_FILE_COUNT = 750
TARGET_SYMBOL = "frozen_large_repo_target"

CASES: tuple[dict[str, str], ...] = (
    {
        "case_id": "index_process_restart",
        "fault": "process exit after a persisted index heartbeat",
    },
    {
        "case_id": "frozen_snapshot_first_token_timeout",
        "fault": "retryable first-token timeout after repository evidence is frozen",
    },
    {
        "case_id": "large_tool_output_context_overflow",
        "fault": "provider context overflow after one large-output tool effect",
    },
    {
        "case_id": "model_wait_process_restart",
        "fault": "process exit while the provider owns a frozen model projection",
    },
)


class _SequenceProvider:
    name = "large-repo-fault-provider"
    model = "controlled-model"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    def complete(self, messages, *, tools=None):
        self.seen.append(list(messages))
        if not self.outcomes:
            raise AssertionError("controlled provider outcomes exhausted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _InterruptingIndex(RepositoryContextIndex):
    """Exit after production progress persistence asks us to scan 250 files."""

    def refresh(self, *, progress=None, progress_every_files=250):
        def interrupt(observed):
            if progress is not None:
                progress(observed)
            if observed.processed_files >= 250:
                raise SystemExit(
                    "benchmark fault: process exited during repository index"
                )

        return super().refresh(
            progress=interrupt,
            progress_every_files=progress_every_files,
        )


def run_large_repository_matrix(
    output_dir: str | Path,
    *,
    repository: str | Path | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="taiyi-large-repo-") as temporary:
        scratch = Path(temporary)
        if repository is None:
            source = scratch / "fixture"
            _build_fixture(source)
            source_kind = "deterministic_fixture"
            effective_query = query or TARGET_SYMBOL
        else:
            source = Path(repository).resolve()
            if not source.is_dir():
                raise FileNotFoundError(f"repository does not exist: {source}")
            source_kind = "pinned_git_checkout"
            effective_query = query or "README"

        seed_db = scratch / "seed" / "repositories.sqlite3"
        seed = RepositoryContextIndex(source, db_path=seed_db)
        indexed = seed.refresh()
        retrieved = seed.retrieve(effective_query, token_budget=4000, max_chunks=8)
        if indexed.file_count < 250:
            raise ValueError(
                "large-repository benchmark requires at least 250 inventoried files"
            )
        if not retrieved.snippets:
            raise ValueError(
                f"benchmark query retrieved no source evidence: {effective_query!r}"
            )
        source_contract = _source_contract(
            source, source_kind, indexed, effective_query
        )
        seed.close()

        manifest = large_repository_manifest(source_contract)
        write_artifact(
            destination / "manifest.json", LARGE_REPO_MANIFEST_SCHEMA, manifest
        )
        receipts = []
        for case in CASES:
            for mode in OPERATING_MODES:
                cell = scratch / "cells" / case["case_id"] / mode
                receipt = _run_cell(
                    case,
                    mode,
                    source,
                    seed_db,
                    effective_query,
                    cell,
                )
                receipts.append(receipt)
                write_artifact(
                    destination / "runs" / f"{case['case_id']}--{mode}.json",
                    LARGE_REPO_RECEIPT_SCHEMA,
                    receipt,
                )

    passed = [item for item in receipts if item["protocol_passed"]]
    report = {
        "measurement_scope": MEASUREMENT_SCOPE,
        "generated_at": time.time(),
        "manifest_digest": manifest["contract_digest"],
        "source": source_contract,
        "case_count": len(CASES),
        "run_count": len(receipts),
        "protocol_passed_count": len(passed),
        "all_protocol_passed": len(passed) == len(receipts),
        "false_completions": sum(bool(item["false_completion"]) for item in receipts),
        "duplicate_effects": sum(int(item["duplicate_effects"]) for item in receipts),
        "by_mode": {
            mode: {
                "runs": sum(item["operating_mode"] == mode for item in receipts),
                "passed": sum(
                    item["operating_mode"] == mode and item["protocol_passed"]
                    for item in receipts
                ),
            }
            for mode in OPERATING_MODES
        },
        "runs": receipts,
        "external_comparison": {
            "status": "NOT_COMPARABLE",
            "reason": (
                "Pi, OpenClaw, and ZCode are not scored until the same pinned checkout, "
                "model transport, context budget, fault boundary, and authoritative "
                "checkpoint/effect receipt can be observed."
            ),
        },
        "ranking_eligible": False,
    }
    write_artifact(destination / "report.json", LARGE_REPO_REPORT_SCHEMA, report)
    (destination / "REPORT.md").write_text(_render_report(report), encoding="utf-8")
    return report


def large_repository_manifest(source: dict[str, Any]) -> dict[str, Any]:
    contract = {
        "measurement_scope": MEASUREMENT_SCOPE,
        "operating_modes": list(OPERATING_MODES),
        "source": source,
        "cases": [dict(case) for case in CASES],
        "reliability_floor": [
            "atomic repository snapshot or no published snapshot",
            "persisted index progress and phase-correct recovery",
            "frozen source projection across provider or process interruption",
            "bounded context overflow recovery",
            "no model retry may replay a governed tool effect",
            "no false completion",
        ],
    }
    return {**contract, "contract_digest": canonical_digest(contract)}


def _run_cell(case, mode, source, seed_db, query, root):
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    error = None
    try:
        case_id = case["case_id"]
        if case_id == "index_process_restart":
            evidence = _index_restart(mode, source, query, root)
        elif case_id == "frozen_snapshot_first_token_timeout":
            evidence = _first_token_timeout(mode, source, seed_db, query, root)
        elif case_id == "large_tool_output_context_overflow":
            evidence = _tool_output_overflow(mode, source, seed_db, query, root)
        else:
            evidence = _model_wait_restart(mode, source, seed_db, query, root)
    except Exception as exc:
        evidence = {"passed": False, "state": "HARNESS_ERROR", "duplicate_effects": 0}
        error = f"{type(exc).__name__}: {exc}"
    claimed_complete = evidence.get("state") == TaskState.COMPLETED.value
    protocol_passed = bool(evidence.get("passed")) and error is None
    return {
        "run_id": f"taiyi-{case['case_id']}-{mode}-{uuid.uuid4().hex[:12]}",
        "case_id": case["case_id"],
        "operating_mode": mode,
        "measurement_status": "MEASURED" if error is None else "ERROR",
        "reported_state": evidence.get("state"),
        "claimed_complete": claimed_complete,
        "false_completion": claimed_complete and not bool(evidence.get("passed")),
        "protocol_passed": protocol_passed,
        "duplicate_effects": int(evidence.get("duplicate_effects", 0)),
        "duration_seconds": max(0.0, time.monotonic() - started),
        "manifest_case_digest": canonical_digest(case),
        "evidence": {key: value for key, value in evidence.items() if key != "passed"},
        "error": error,
    }


def _index_restart(mode, source, query, root):
    state = root / "state"
    workspace = root / "workspace"
    workspace.mkdir()
    db = root / "index" / "repositories.sqlite3"
    interrupted_index = _InterruptingIndex(source, db_path=db)
    unused = _SequenceProvider([LLMResponse(text="must not run before index settles")])
    first = _gateway(state, workspace, interrupted_index, unused, mode)
    try:
        first.submit(f"Inspect {query} using source evidence.", operating_mode=mode)
    except SystemExit as exc:
        if "repository index" not in str(exc):
            raise
    if unused.seen:
        raise AssertionError("model was called before interrupted index settled")
    checkpoint_path = next((state / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    before = first.runtime.run_store.read_events(task_id)
    heartbeat = [
        event for event in before if event["event"] == "repository_index_heartbeat"
    ][-1]
    if interrupted_index.latest() is not None:
        raise AssertionError("interrupted transaction published a partial snapshot")
    first.runtime.run_store.release_task_lease(task_id)

    recovered_provider = _SequenceProvider(
        [LLMResponse(text="source evidence recovered")]
    )
    recovered_index = RepositoryContextIndex(source, db_path=db)
    restarted = _gateway(state, workspace, recovered_index, recovered_provider, mode)
    settled = _wait_settled(state, task_id, timeout=20)
    _join_recovery(restarted, task_id)
    events = restarted.runtime.run_store.read_events(task_id)
    visible = "\n".join(message.content for message in recovered_provider.seen[0])
    passed = (
        settled["context"]["state"] == TaskState.COMPLETED.value
        and heartbeat["payload"]["progress"]["processed_files"] == 250
        and settled["context"]["repository_context"]["complete"] is True
        and query in visible
        and sum(event["event"] == "run_recovered" for event in events) == 1
    )
    return {
        "passed": passed,
        "state": settled["context"]["state"],
        "duplicate_effects": 0,
        "interrupted_phase": interrupted["context"]["phase"],
        "heartbeat_processed_files": heartbeat["payload"]["progress"][
            "processed_files"
        ],
        "partial_snapshot_published": False,
        "recovered_snapshot_id": settled["context"]["repository_context"][
            "snapshot_id"
        ],
        "run_recovered_count": sum(
            event["event"] == "run_recovered" for event in events
        ),
    }


def _first_token_timeout(mode, source, seed_db, query, root):
    index = _copy_seed_index(source, seed_db, root / "index.sqlite3")
    failure = LLMRequestError(
        LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT,
        "controlled first-token timeout",
        retryable=True,
        phase="first_token",
    )
    provider = _SequenceProvider([failure, LLMResponse(text="retried frozen evidence")])
    gateway = _gateway(root / "state", root / "workspace", index, provider, mode)
    ctx = gateway.submit(f"Inspect {query} using source evidence.", operating_mode=mode)
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    failures = [event for event in events if event["event"] == "llm_attempt_failed"]
    frozen = [
        canonical_digest([(m.role, m.content) for m in seen]) for seen in provider.seen
    ]
    passed = (
        ctx.state is TaskState.COMPLETED
        and len(provider.seen) == 2
        and len(failures) == 1
        and failures[0]["payload"]["failure_kind"] == "LLM_FIRST_TOKEN_TIMEOUT"
        and frozen[0] == frozen[1]
        and ctx.repository_context["snapshot_id"] == index.latest().snapshot_id
    )
    return {
        "passed": passed,
        "state": ctx.state.value,
        "duplicate_effects": 0,
        "llm_calls": len(provider.seen),
        "failure_kind": failures[0]["payload"]["failure_kind"] if failures else None,
        "frozen_projection_reused": len(set(frozen)) == 1,
        "snapshot_id": ctx.repository_context["snapshot_id"],
        "directory_chunks_rebuilt": ctx.repository_context["directory_chunks_rebuilt"],
    }


def _tool_output_overflow(mode, source, seed_db, query, root):
    index = _copy_seed_index(source, seed_db, root / "index.sqlite3")
    workspace = root / "workspace"
    workspace.mkdir()
    code = (
        "from pathlib import Path; "
        "Path('marker.txt').open('a').write('once\\n'); "
        "print('OUTPUT-HEAD' + ('z' * 100000) + 'OUTPUT-TAIL')"
    )
    overflow = LLMRequestError(
        LLMErrorKind.CONTEXT_OVERFLOW,
        "controlled provider context overflow",
        retryable=False,
    )
    provider = _SequenceProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(f"shell:{shlex.quote(sys.executable)}", ["-c", code])
                ]
            ),
            overflow,
            LLMResponse(text="one effect and compacted evidence verified"),
        ]
    )
    gateway = _gateway(root / "state", workspace, index, provider, mode)
    ctx = gateway.submit(
        f"Inspect {query}, write marker.txt once, then finish.", operating_mode=mode
    )
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    marker = workspace / "marker.txt"
    lines = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    compactions = sum(event["event"] == "context_compacted" for event in events)
    starts = sum(event["event"] == "tool_started" for event in events)
    passed = (
        ctx.state is TaskState.COMPLETED
        and lines == ["once"]
        and starts == 1
        and compactions == 1
        and ctx.context_state["overflow_recoveries"] == 1
        and len(provider.seen) == 3
    )
    return {
        "passed": passed,
        "state": ctx.state.value,
        "duplicate_effects": max(0, lines.count("once") - 1),
        "marker_line_count": len(lines),
        "tool_started_count": starts,
        "context_compaction_count": compactions,
        "overflow_recoveries": ctx.context_state.get("overflow_recoveries", 0),
        "stdout_bytes": ctx.step_results[0].stdout_bytes if ctx.step_results else 0,
        "output_truncated": ctx.step_results[0].output_truncated
        if ctx.step_results
        else False,
    }


def _model_wait_restart(mode, source, seed_db, query, root):
    state = root / "state"
    workspace = root / "workspace"
    workspace.mkdir()
    index = _copy_seed_index(source, seed_db, root / "index.sqlite3")
    crashing = _SequenceProvider([SystemExit("benchmark fault: provider owner exited")])
    first = _gateway(state, workspace, index, crashing, mode)
    try:
        first.submit(f"Inspect {query} using source evidence.", operating_mode=mode)
    except SystemExit as exc:
        if "provider owner" not in str(exc):
            raise
    checkpoint_path = next((state / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    frozen = canonical_digest(interrupted["continuation"]["model_messages"])
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="continued frozen model turn")])
    restarted_index = RepositoryContextIndex(source, db_path=root / "index.sqlite3")
    restarted = _gateway(state, workspace, restarted_index, recovered, mode)
    settled = _wait_settled(state, task_id)
    _join_recovery(restarted, task_id)
    replayed = canonical_digest(
        [
            {"role": message.role, "content": message.content}
            for message in recovered.seen[0]
        ]
    )
    events = restarted.runtime.run_store.read_events(task_id)
    passed = (
        settled["context"]["state"] == TaskState.COMPLETED.value
        and interrupted["context"]["phase"] == RunPhase.LLM_WAITING.value
        and frozen == replayed
        and sum(event["event"] == "run_recovered" for event in events) == 1
    )
    return {
        "passed": passed,
        "state": settled["context"]["state"],
        "duplicate_effects": 0,
        "interrupted_phase": interrupted["context"]["phase"],
        "frozen_projection_replayed": frozen == replayed,
        "run_recovered_count": sum(
            event["event"] == "run_recovered" for event in events
        ),
        "snapshot_id": settled["context"]["repository_context"]["snapshot_id"],
    }


def _gateway(state, workspace, index, provider, mode):
    return build_gateway(
        base_dir=state,
        mode="agent",
        operating_mode=mode,
        executor=SandboxExecutor(
            workspace,
            job_dir=state / "jobs",
            output_limit=4096,
            artifact_limit=131072,
        ),
        provider=provider,
        context_engine=ContextEngine(
            repository=index,
            base_dir=state,
            context_window_tokens=6000,
            response_reserve_tokens=1000,
            tool_result_max_tokens=500,
        ),
        validator=False,
        llm_sleep=lambda _seconds: None,
    )


def _copy_seed_index(source, seed_db, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed_db, destination)
    return RepositoryContextIndex(source, db_path=destination)


def _wait_settled(state, task_id, timeout=10):
    path = state / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise TimeoutError(f"large-repository task {task_id} did not settle")


def _join_recovery(gateway, task_id):
    thread = gateway.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)


def _build_fixture(root):
    root.mkdir(parents=True)
    for number in range(FIXTURE_FILE_COUNT):
        package = root / f"package_{number // 100:02d}"
        package.mkdir(exist_ok=True)
        content = (
            "def frozen_large_repo_target():\n"
            "    return 'source-backed-and-recoverable'\n"
            if number == 611
            else f"def generated_symbol_{number}():\n    return {number}\n"
        )
        (package / f"module_{number:04d}.py").write_text(content, encoding="utf-8")


def _source_contract(source, source_kind, indexed, query):
    return {
        "kind": source_kind,
        "origin": _safe_origin(
            _git_value(source, ["config", "--get", "remote.origin.url"])
        ),
        "git_head": indexed.git_head,
        "snapshot_id": indexed.snapshot_id,
        "file_count": indexed.file_count,
        "chunk_count": indexed.chunk_count,
        "skipped_files": indexed.skipped_files,
        "unsearchable_files": indexed.skipped_files,
        "omitted_files": indexed.omitted_files,
        "inventory_complete": indexed.complete,
        "searchable_complete": indexed.complete and indexed.skipped_files == 0,
        "complete": indexed.complete,
        "query": query,
    }


def _git_value(root, args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    value = result.stdout.strip()
    return value or None


def _safe_origin(origin):
    """Keep source identity without leaking local paths or remote credentials."""

    if not origin:
        return None
    if origin.startswith(("/", "./", "../", "file://")):
        return "LOCAL_OR_REDACTED"
    parsed = urlsplit(origin)
    if parsed.scheme in {"http", "https"}:
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, hostname + port, parsed.path, "", ""))
    return origin


def _render_report(report):
    source = report["source"]
    lines = [
        "# TaiYi large-repository combined resilience baseline",
        "",
        f"- Source: `{source['kind']}`",
        f"- Git HEAD: `{source.get('git_head') or 'not-applicable'}`",
        f"- Snapshot: `{source['snapshot_id']}`",
        f"- Indexed files/chunks: {source['file_count']} / {source['chunk_count']}",
        f"- Protocol passes: {report['protocol_passed_count']}/{report['run_count']}",
        f"- False completions: {report['false_completions']}",
        f"- Duplicate effects: {report['duplicate_effects']}",
        "",
        "| Case | Quality | Balanced | Efficiency |",
        "| --- | --- | --- | --- |",
    ]
    for case in CASES:
        cells = []
        for mode in OPERATING_MODES:
            receipt = next(
                item
                for item in report["runs"]
                if item["case_id"] == case["case_id"] and item["operating_mode"] == mode
            )
            cells.append("PASS" if receipt["protocol_passed"] else "FAIL")
        lines.append(f"| {case['case_id']} | {' | '.join(cells)} |")
    lines.extend(
        [
            "",
            "External harnesses remain NOT_COMPARABLE until they expose the same pinned "
            "source, model, context, fault, checkpoint, and effect receipt boundary.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "CASES",
    "FIXTURE_FILE_COUNT",
    "large_repository_manifest",
    "run_large_repository_matrix",
]
