import { useEffect, useState } from "react";
import { api } from "../api";

interface Pending {
  id: number;
  kind: string;
  rule_id?: string;
  name?: string;
  scenario?: string;
  tool?: string;
  tools?: string[];
  occurrences?: number;
  rationale?: string;
}
interface Trajectory {
  task_id: string;
  scenario: string;
  state: string;
  prompt: string;
  tools: string[];
  steps: { tool: string; verdict: string; executed: boolean; output: string | null }[];
  ts: number;
}

export default function Ooda() {
  const [pending, setPending] = useState<Pending[]>([]);
  const [report, setReport] = useState<any>(null);
  const [trajs, setTrajs] = useState<Trajectory[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const load = async () => {
    try {
      const [p, r, t] = await Promise.all([
        api.listPending().catch(() => ({ pending: [] })),
        api.report().catch(() => null),
        api.trajectories().catch(() => ({ trajectories: [] })),
      ]);
      setPending(p.pending || []);
      setReport(r);
      setTrajs(t.trajectories || []);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const resolve = async (id: number, action: "approve" | "reject") => {
    setBusy(id);
    try {
      await api.resolveReview(id, action);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <h1>OODA 审查</h1>
      <p className="subtitle">
        外循环自动产出的规则 / 技能建议。人审 approve 后落盘到 rules/auto、skills/auto，重启即生效——运行时绝不热加载。
      </p>
      {error && <div className="error">{error}</div>}

      {report && (
        <div className="card">
          <div className="row">
            <Stat label="任务总数" value={report.tasks} />
            <Stat label="失败" value={report.failures} />
            <Stat label="待审建议" value={report.pending_reviews} />
            <Stat label="回归用例" value={report.regression_cases} />
          </div>
        </div>
      )}

      <h2>待审建议 ({pending.length})</h2>
      {pending.length === 0 ? (
        <p className="muted">没有待审建议。失败模式累积达到阈值后会自动出现。</p>
      ) : (
        pending.map((p) => (
          <div className="card" key={p.id}>
            <div className="row">
              <span className={`badge ${p.kind === "rule" ? "warn" : "ok"}`}>{p.kind}</span>
              <strong className="mono">{p.rule_id || p.name}</strong>
              <span className="badge">{p.scenario}</span>
              {p.tool && <span className="badge">{p.tool}</span>}
              <span className="muted">× {p.occurrences}</span>
            </div>
            {p.rationale && <p className="mono" style={{ margin: "8px 0" }}>{p.rationale}</p>}
            {p.tools && <p className="mono">tools: {p.tools.join(" → ")}</p>}
            <div className="row gap" style={{ marginTop: 10 }}>
              <button onClick={() => resolve(p.id, "approve")} disabled={busy === p.id}>
                批准落盘
              </button>
              <button className="danger" onClick={() => resolve(p.id, "reject")} disabled={busy === p.id}>
                拒绝
              </button>
            </div>
          </div>
        ))
      )}

      <h2>历史轨迹 ({trajs.length})</h2>
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>场景</th>
              <th>状态</th>
              <th>步骤</th>
              <th>Prompt</th>
            </tr>
          </thead>
          <tbody>
            {trajs.map((t) => (
              <tr key={t.task_id}>
                <td className="mono">{t.task_id.slice(0, 16)}</td>
                <td>{t.scenario}</td>
                <td>
                  <span className={`badge ${t.state === "COMPLETED" ? "ok" : t.state === "FAILED" || t.state === "REJECTED" ? "danger" : "warn"}`}>
                    {t.state}
                  </span>
                </td>
                <td className="mono">{t.steps.length}</td>
                <td className="muted" style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.prompt}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ textAlign: "center", flex: 1 }}>
      <div style={{ fontSize: 24, fontFamily: "DM Serif Display, serif", color: "var(--primary)" }}>{value}</div>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
    </div>
  );
}
