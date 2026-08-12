"""Benchmark orchestration, aggregation, and artifact emission."""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from taiyi.benchmark.adapters import probe_external_harnesses, probe_set_digest
from taiyi.benchmark.protocol import TaiYiProtocolAdapter, cases_manifest, protocol_cases
from taiyi.benchmark.schema import (
    BENCHMARK_SCHEMA,
    REPORT_SCHEMA,
    RECEIPT_SCHEMA,
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
    "run_protocol_matrix",
    "write_probe_report",
]
