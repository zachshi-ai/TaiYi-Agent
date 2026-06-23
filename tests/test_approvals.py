"""Human approval & resume (M17)."""
from __future__ import annotations

import json

from taiyi.approvals import ApprovalStore
from taiyi.core.audit import AuditLog
from taiyi.gateway import GatewayApp, build_gateway
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.validation import ValidationEngine


def runtime():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return TaskRuntime(sched, audit_log=audit, validator=ValidationEngine(), approvals=ApprovalStore())


# --- Suspend → approve → resume to completion --------------------------------

def test_suspended_task_resumes_on_approval():
    rt = runtime()
    ctx = rt.run("帮我生成上周周报", "ops.report")  # sql allowed, notify needs review
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert len(rt.approvals) == 1
    assert [s.step.tool for s in ctx.executed_steps] == ["sql:query"]

    resumed = rt.resume(ctx.approval_id, approve=True)
    assert resumed.state is TaskState.COMPLETED
    assert [s.step.tool for s in resumed.executed_steps] == ["sql:query", "notify:feishu"]
    assert len(rt.approvals) == 0  # cleared


def test_rejection_marks_task_rejected():
    rt = runtime()
    ctx = rt.run("git push 到 origin main", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW
    resumed = rt.resume(ctx.approval_id, approve=False)
    assert resumed.state is TaskState.REJECTED
    assert "rejected by human" in resumed.final_output


def test_unknown_approval_raises():
    rt = runtime()
    try:
        rt.resume("nope", approve=True)
        assert False, "expected KeyError"
    except KeyError:
        pass


# --- Gateway approval endpoints ----------------------------------------------

def test_gateway_approval_flow():
    app = GatewayApp(build_gateway())
    # Submit a task that suspends.
    status, data = app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "帮我生成上周周报", "scenario": "ops.report"}))
    assert data["state"] == "NEEDS_REVIEW"
    approval_id = data["approval_id"]

    # It appears in the pending list.
    _, listing = app.handle("GET", "/v1/approvals", {}, "")
    assert any(p["approval_id"] == approval_id for p in listing["pending"])

    # Approve it → the task completes.
    status, resolved = app.handle(
        "POST", "/v1/approvals/resolve", {}, json.dumps({"approval_id": approval_id, "decision": "approve"})
    )
    assert status == 200
    assert resolved["state"] == "COMPLETED"

    # And it is gone from the pending list.
    _, listing2 = app.handle("GET", "/v1/approvals", {}, "")
    assert listing2["pending"] == []
