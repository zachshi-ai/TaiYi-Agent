import { useEffect, useState } from "react";
import { api } from "../api";

export default function Observe() {
  const [tab, setTab] = useState<"sessions" | "memories" | "skills" | "metrics">("sessions");
  const [sessions, setSessions] = useState<any[]>([]);
  const [memories, setMemories] = useState<any[]>([]);
  const [skills, setSkills] = useState<any[]>([]);
  const [metrics, setMetrics] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [s, m, sk, me] = await Promise.all([
          api.sessions().catch(() => ({ sessions: [] })),
          api.memories().catch(() => ({ memories: [] })),
          api.skills().catch(() => ({ skills: [] })),
          api.metricsJson().catch(() => null),
        ]);
        setSessions(s.sessions || []);
        setMemories(m.memories || []);
        setSkills(sk.skills || []);
        setMetrics(me);
      } catch (e: any) {
        setError(e.message);
      }
    })();
  }, []);

  return (
    <div>
      <h1>记忆 / 指标</h1>
      <p className="subtitle">观察面板：会话历史、长期记忆、技能目录、运行指标。只读。</p>
      {error && <div className="error">{error}</div>}

      <div className="row" style={{ marginBottom: 16 }}>
        {(["sessions", "memories", "skills", "metrics"] as const).map((t) => (
          <button
            key={t}
            className={tab === t ? "" : "secondary"}
            onClick={() => setTab(t)}
          >
            {label(t)}
          </button>
        ))}
      </div>

      {tab === "sessions" && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead><tr><th>Session</th><th>消息数</th><th>最后活跃</th></tr></thead>
            <tbody>
              {sessions.length === 0 && <tr><td colSpan={3} className="muted">无会话</td></tr>}
              {sessions.map((s) => (
                <tr key={s.session_id}>
                  <td className="mono">{s.session_id}</td>
                  <td>{s.msg_count}</td>
                  <td className="muted">{s.last_ts ? new Date(s.last_ts * 1000).toLocaleString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "memories" && (
        <div>
          {memories.length === 0 && <p className="muted">无长期记忆。</p>}
          {memories.map((m) => (
            <div className="card" key={m.id}>
              <div className="row">
                {(m.tags || []).map((t: string) => <span key={t} className="badge">{t}</span>)}
                <span className="badge ok">重要性 {m.importance}</span>
                <span className="muted">{m.source_task_id || ""}</span>
              </div>
              <p style={{ margin: "8px 0 0" }}>{m.content}</p>
            </div>
          ))}
        </div>
      )}

      {tab === "skills" && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead><tr><th>技能</th><th>标签</th><th>摘要</th></tr></thead>
            <tbody>
              {skills.length === 0 && <tr><td colSpan={3} className="muted">无技能</td></tr>}
              {skills.map((s) => (
                <tr key={s.name}>
                  <td className="mono">{s.name}</td>
                  <td>{(s.tags || []).join(", ")}</td>
                  <td className="muted">{s.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "metrics" && (
        <div>
          {!metrics ? (
            <p className="muted">指标未启用。</p>
          ) : (
            Object.entries(metrics).map(([name, m]: [string, any]) => (
              <div className="card" key={name}>
                <div className="row">
                  <strong className="mono">{name}</strong>
                  <span className="badge">{m.type}</span>
                </div>
                <p className="muted" style={{ margin: "4px 0 8px" }}>{m.help}</p>
                {m.type === "histogram" ? (
                  <div className="mono">
                    count={m.count} sum={Number(m.sum).toFixed(3)}s
                    <br />
                    buckets: {m.buckets.map((b: number, i: number) => `≤${b}:${m.bucket_counts[i]}`).join("  ")}
                  </div>
                ) : (
                  <div className="mono">
                    {(m.series || []).map((s: any, i: number) => (
                      <div key={i}>
                        {Object.entries(s.labels).map(([k, v]) => `${k}=${v}`).join(",") || "(total)"} → {s.value}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function label(t: string) {
  return { sessions: "会话", memories: "记忆库", skills: "技能", metrics: "指标" }[t] || t;
}
