import { useEffect, useState } from "react";
import { api } from "../api";

interface Approval {
  approval_id: string;
  task_id: string;
  tool: string;
  reason: string;
  scenario: string;
  held_step: number;
  total_steps: number;
}

export default function Approvals() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    try {
      const d = await api.listApprovals();
      setApprovals(d.pending || []);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const resolve = async (id: string, decision: "approve" | "reject") => {
    setBusy(id);
    try {
      await api.resolveApproval(id, decision);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <h1>人工审批</h1>
      <p className="subtitle">
        治理判定 NEEDS_REVIEW 的任务挂起于此。人审 approve 后，该步骤会重新经 governance 校验——若规则在此期间变严为 DENY，仍会被拒绝。
      </p>
      {error && <div className="error">{error}</div>}
      {approvals.length === 0 ? (
        <p className="muted">没有待审批任务。</p>
      ) : (
        approvals.map((a) => (
          <div className="card" key={a.approval_id}>
            <div className="row">
              <strong className="mono">{a.tool}</strong>
              <span className="badge warn">NEEDS_REVIEW</span>
              <span className="badge">{a.scenario}</span>
              <span className="muted">step {a.held_step + 1}/{a.total_steps}</span>
            </div>
            <p className="mono" style={{ margin: "8px 0" }}>{a.reason}</p>
            <p className="muted">task: {a.task_id} · approval: {a.approval_id}</p>
            <div className="row gap" style={{ marginTop: 10 }}>
              <button onClick={() => resolve(a.approval_id, "approve")} disabled={busy === a.approval_id}>
                批准执行
              </button>
              <button className="danger" onClick={() => resolve(a.approval_id, "reject")} disabled={busy === a.approval_id}>
                拒绝
              </button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
