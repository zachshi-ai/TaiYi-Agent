"""Benchmark orchestration, aggregation, and artifact emission."""
from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from taiyi.benchmark.adapters import probe_external_harnesses, probe_set_digest
from taiyi.benchmark.comparative import (
    blocked_external_receipts,
    comparative_manifest,
    run_pi_cell,
    run_taiyi_cell,
)
from taiyi.benchmark.model_fixture import ControlledModelServer
from taiyi.benchmark.protocol import TaiYiProtocolAdapter, cases_manifest, protocol_cases
from taiyi.benchmark.schema import (
    BENCHMARK_SCHEMA,
    COMPARATIVE_MANIFEST_SCHEMA,
    COMPARATIVE_RECEIPT_SCHEMA,
    COMPARATIVE_REPORT_SCHEMA,
    REPORT_SCHEMA,
    RECEIPT_SCHEMA,
    ComparativeReceipt,
    MeasurementStatus,
    RunReceipt,
    canonical_digest,
    write_artifact,
)


OPERATING_MODES = ("quality", "balanced", "efficiency")


def write_probe_report(output_dir: str | Path) -> dict[str, Any]:
    """Persist capability facts for external harnesses without assigning scores."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    probes = probe_external_harnesses()
    payload = {
        "observed_at": time.time(),
        "probes": [probe.to_dict() for probe in probes],
        "probe_set_digest": probe_set_digest(probes),
        "scoring_rule": (
            "UNAVAILABLE and NOT_COMPARABLE cells are reported but excluded from scores"
        ),
    }
    write_artifact(destination / "external-harness-probes.json", BENCHMARK_SCHEMA, payload)
    return payload


def run_protocol_matrix(output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = cases_manifest()
    write_artifact(destination / "manifest.json", BENCHMARK_SCHEMA, manifest)
    probe_payload = write_probe_report(destination)

    adapter = TaiYiProtocolAdapter()
    receipts: list[RunReceipt] = []
    with tempfile.TemporaryDirectory(prefix="taiyi-harness-benchmark-") as temporary:
        scratch = Path(temporary)
        for case in protocol_cases():
            for mode in OPERATING_MODES:
                run_root = scratch / case.case_id / mode
                receipt = adapter.run(case, mode, run_root)
                receipts.append(receipt)
                write_artifact(
                    destination / "runs" / f"{case.case_id}--{mode}.json",
                    RECEIPT_SCHEMA,
                    receipt.to_dict(),
                )

    report = build_report(receipts, manifest=manifest, probe_payload=probe_payload)
    write_artifact(destination / "report.json", REPORT_SCHEMA, report)
    (destination / "REPORT.md").write_text(render_markdown_report(report), encoding="utf-8")
    return report


def run_comparative_smoke(
    output_dir: str | Path,
    *,
    pi_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Run same-endpoint transport/tool cells without claiming model quality."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for harness_id in ("taiyi", "pi"):
        for log_name in ("stdout.log", "stderr.log"):
            (destination / "raw" / harness_id / log_name).unlink(missing_ok=True)
    with ControlledModelServer() as server:
        manifest = comparative_manifest(server.policy_digest)
        write_artifact(
            destination / "manifest.json",
            COMPARATIVE_MANIFEST_SCHEMA,
            manifest,
        )
        with tempfile.TemporaryDirectory(prefix="taiyi-comparative-benchmark-") as temporary:
            scratch = Path(temporary)
            receipts = [
                run_taiyi_cell(
                    server=server,
                    manifest=manifest,
                    run_root=scratch / "taiyi",
                    artifact_dir=destination / "raw" / "taiyi",
                )
            ]
            discovered_pi = str(pi_executable) if pi_executable else shutil.which("pi")
            receipts.append(run_pi_cell(
                pi_executable=(discovered_pi or scratch / "missing-pi"),
                server=server,
                manifest=manifest,
                run_root=scratch / "pi",
                artifact_dir=destination / "raw" / "pi",
            ))
            receipts.extend(blocked_external_receipts(manifest, scratch))

        for receipt in receipts:
            write_artifact(
                destination / "runs" / f"{receipt.harness_id}.json",
                COMPARATIVE_RECEIPT_SCHEMA,
                receipt.to_dict(),
            )
        report = build_comparative_report(receipts, manifest)
        write_artifact(
            destination / "report.json",
            COMPARATIVE_REPORT_SCHEMA,
            report,
        )
        (destination / "REPORT.md").write_text(
            render_comparative_markdown(report), encoding="utf-8"
        )
        return report


def build_comparative_report(
    receipts: Iterable[ComparativeReceipt],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    values = list(receipts)
    signatures_match = all(
        item.comparability_signature == manifest["comparability_signature"]
        for item in values
    )
    comparable = [
        item for item in values
        if (
            item.comparable
            and item.measurement_status is MeasurementStatus.MEASURED
            and item.comparability_signature == manifest["comparability_signature"]
        )
    ]
    return {
        "measurement_scope": manifest["measurement_scope"],
        "generated_at": time.time(),
        "case_id": manifest["case_id"],
        "model_id": manifest["model_id"],
        "comparability_signature": manifest["comparability_signature"],
        "signatures_match": signatures_match,
        "cell_count": len(values),
        "comparable_cell_count": len(comparable),
        "all_comparable_cells_passed": signatures_match and bool(comparable) and all(
            item.task_passed
            and item.claimed_complete
            and item.budget_passed
            and not item.false_completion
            for item in comparable
        ),
        "false_completions": sum(item.false_completion for item in values),
        "ranking_eligible": False,
        "cells": [item.to_dict() for item in values],
        "claim_boundary": list(manifest["claim_boundary"]),
    }


def render_comparative_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# TaiYi Controlled Cross-Harness Smoke",
        "",
        f"- Measurement scope: `{report['measurement_scope']}`",
        f"- Model endpoint identity: `{report['model_id']}`",
        f"- Comparable cells: {report['comparable_cell_count']} / {report['cell_count']}",
        f"- Comparability signatures match: {'YES' if report['signatures_match'] else 'NO'}",
        f"- Comparable cells passed: {'YES' if report['all_comparable_cells_passed'] else 'NO'}",
        f"- False completions: {report['false_completions']}",
        f"- Ranking eligible: {'YES' if report['ranking_eligible'] else 'NO'}",
        "",
        "| Harness | Status | Comparable | Task passed | Budget passed | Model requests | Tool calls | Blocker |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for cell in report["cells"]:
        blocker = "; ".join(cell["blockers"]) or cell.get("error") or "-"
        lines.append(
            f"| {cell['harness_id']} | {cell['measurement_status']} | "
            f"{'yes' if cell['comparable'] else 'no'} | "
            f"{'yes' if cell['task_passed'] else 'no'} | "
            f"{'yes' if cell['budget_passed'] else 'no'} | "
            f"{cell['model_requests']} | {cell['tool_calls']} | {blocker} |"
        )
    lines.extend(["", "## Claim boundary", ""])
    lines.extend(f"- {item}" for item in report["claim_boundary"])
    lines.append("")
    return "\n".join(lines)


def build_report(
    receipts: Iterable[RunReceipt],
    *,
    manifest: dict[str, Any],
    probe_payload: dict[str, Any],
) -> dict[str, Any]:
    values = list(receipts)
    measured = [item for item in values if item.measurement_status is MeasurementStatus.MEASURED]
    by_mode: dict[str, Any] = {}
    for mode in OPERATING_MODES:
        group = [item for item in measured if item.operating_mode == mode]
        faulted = [item for item in group if item.fault_injected]
        by_mode[mode] = {
            "runs": len(group),
            "task_pass_rate": _rate(item.task_passed for item in group),
            "protocol_pass_rate": _rate(item.protocol_passed for item in group),
            "false_completion_rate": _rate(item.false_completion for item in group),
            "fault_recovery_rate": _rate(item.recovered for item in faulted),
            "human_handoff_rate": _rate(item.human_handoffs > 0 for item in group),
            "duplicate_effects": sum(item.duplicate_effects for item in group),
            "mean_duration_seconds": (
                statistics.fmean(item.duration_seconds for item in group) if group else None
            ),
            "total_llm_calls": sum(item.llm_calls for item in group),
            "total_connector_attempts": sum(item.connector_attempts for item in group),
        }

    comparable_signature = canonical_digest({
        "provider_id": manifest["provider_id"],
        "cases_digest": manifest["cases_digest"],
        "modes": list(OPERATING_MODES),
        "measurement_scope": manifest["measurement_scope"],
    })
    return {
        "measurement_scope": manifest["measurement_scope"],
        "generated_at": time.time(),
        "harness": "taiyi",
        "harness_version": TaiYiProtocolAdapter.harness_version,
        "provider_id": manifest["provider_id"],
        "cases_digest": manifest["cases_digest"],
        "comparability_signature": comparable_signature,
        "run_count": len(values),
        "measured_run_count": len(measured),
        "all_protocol_passed": bool(values) and len(measured) == len(values) and all(
            item.protocol_passed for item in measured
        ),
        "false_completions": sum(item.false_completion for item in measured),
        "duplicate_effects": sum(item.duplicate_effects for item in measured),
        "by_mode": by_mode,
        "runs": [item.to_dict() for item in values],
        "external_harness_probes": probe_payload,
        "claim_boundary": [
            "This report measures TaiYi protocol behavior with a deterministic scripted provider.",
            "It does not establish model quality or a cross-harness ranking.",
            "Only external cells with the same comparability signature may be ranked together.",
        ],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# TaiYi Harness Protocol Benchmark",
        "",
        f"- Measurement scope: `{report['measurement_scope']}`",
        f"- Provider: `{report['provider_id']}`",
        f"- Measured runs: {report['measured_run_count']}",
        f"- Protocol conformance: {'PASS' if report['all_protocol_passed'] else 'FAIL'}",
        f"- False completions: {report['false_completions']}",
        f"- Duplicate effects: {report['duplicate_effects']}",
        "",
        "| Mode | Task pass | Protocol pass | Fault recovery | Human handoff | LLM calls | Connector attempts |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in OPERATING_MODES:
        item = report["by_mode"][mode]
        lines.append(
            f"| {mode} | {_percent(item['task_pass_rate'])} | "
            f"{_percent(item['protocol_pass_rate'])} | "
            f"{_percent(item['fault_recovery_rate'])} | "
            f"{_percent(item['human_handoff_rate'])} | "
            f"{item['total_llm_calls']} | {item['total_connector_attempts']} |"
        )
    lines.extend([
        "",
        "## Claim boundary",
        "",
        *[f"- {line}" for line in report["claim_boundary"]],
        "",
        "## External harness capability cells",
        "",
        "| Harness | Status | Version | Reason |",
        "| --- | --- | --- | --- |",
    ])
    for probe in report["external_harness_probes"]["probes"]:
        lines.append(
            f"| {probe['harness_id']} | {probe['status']} | "
            f"{probe.get('version') or '-'} | {probe.get('reason') or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _rate(values: Iterable[bool]) -> float | None:
    items = list(values)
    return (sum(bool(item) for item in items) / len(items)) if items else None


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


__all__ = [
    "OPERATING_MODES",
    "build_report",
    "render_markdown_report",
    "run_comparative_smoke",
    "run_protocol_matrix",
    "write_probe_report",
]
