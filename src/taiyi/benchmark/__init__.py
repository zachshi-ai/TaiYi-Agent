"""Controlled reliability benchmark for TaiYi and external agent harnesses."""

from taiyi.benchmark.adapters import CommandHarnessAdapter, probe_external_harnesses
from taiyi.benchmark.protocol import (
    TaiYiProtocolAdapter,
    cases_manifest,
    protocol_cases,
)
from taiyi.benchmark.runner import (
    run_comparative_smoke,
    run_protocol_matrix,
    write_probe_report,
)
from taiyi.benchmark.schema import (
    BENCHMARK_SCHEMA,
    COMPARATIVE_MANIFEST_SCHEMA,
    COMPARATIVE_RECEIPT_SCHEMA,
    COMPARATIVE_REPORT_SCHEMA,
    RECEIPT_SCHEMA,
    REPORT_SCHEMA,
    TOOL_FAULT_MANIFEST_SCHEMA,
    TOOL_FAULT_RECEIPT_SCHEMA,
    TOOL_FAULT_REPORT_SCHEMA,
    BenchmarkCase,
    ComparativeReceipt,
    ExpectedOutcome,
    HarnessProbe,
    MeasurementStatus,
    RunReceipt,
    verify_artifact,
)
from taiyi.benchmark.tool_faults import run_tool_fault_matrix, tool_fault_manifest

__all__ = [
    "BENCHMARK_SCHEMA",
    "COMPARATIVE_MANIFEST_SCHEMA",
    "COMPARATIVE_RECEIPT_SCHEMA",
    "COMPARATIVE_REPORT_SCHEMA",
    "REPORT_SCHEMA",
    "RECEIPT_SCHEMA",
    "TOOL_FAULT_MANIFEST_SCHEMA",
    "TOOL_FAULT_RECEIPT_SCHEMA",
    "TOOL_FAULT_REPORT_SCHEMA",
    "BenchmarkCase",
    "CommandHarnessAdapter",
    "ComparativeReceipt",
    "ExpectedOutcome",
    "HarnessProbe",
    "MeasurementStatus",
    "RunReceipt",
    "TaiYiProtocolAdapter",
    "cases_manifest",
    "probe_external_harnesses",
    "protocol_cases",
    "run_protocol_matrix",
    "run_comparative_smoke",
    "run_tool_fault_matrix",
    "tool_fault_manifest",
    "verify_artifact",
    "write_probe_report",
]
