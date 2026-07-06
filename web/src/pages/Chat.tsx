import { useEffect, useState } from "react";
import { api } from "../api";

interface Step {
  tool: string;
  args: string[];
  verdict: string;
  reason?: string;
  matched_rule_id?: string | null;
  executed: boolean;
  output: string | null;
}
interface TaskResult {
  task_id: string;
  state: string;
  scenario: string;
  final_output: string | null;
  approval_id: string | null;
  steps: Step[];
}
interface Session {
  session_id: string;
  msg_count: number;
  last_ts: number;
}

export default function Chat() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("s1");
  const [newSession, setNewSession] = useState("");
  const [prompt, setPrompt] = useState("");
  const [scenario, setScenario] = useState("");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [lastResult, setLastResult] = useState<TaskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadSessions = async () => {
    try {
      const d = await api.sessions();
      setSessions(d.sessions || []);
    } catch {
      /* ignore */
    }
  };

  const loadMessages = async (id: string) => {
    try {
      const d = await api.sessionMessages(id);
      setMessages(d.messages || []);
    } catch {
      setMessages([]);
    }
  };

  useEffect(() => {
    loadSessions();
  }, []);

  useEffect(() => {
    if (sessionId) loadMessages(sessionId);
  }, [sessionId]);

  const send = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setError("");
    setLastResult(null);
    try {
      const res: TaskResult = await api.submitTask(prompt, scenario || undefined, sessionId);
      setLastResult(res);
      setMessages((m) => [...m, { role: "user", content: prompt }]);
      if (res.state === "COMPLETED" && res.final_output) {
        setMessages((m) => [...m, { role: "assistant", content: res.final_output! }]);
      }
      setPrompt("");
      loadSessions();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const createSession = () => {
    const id = newSession.trim() || `s${Date.now()}`;
    setSessionId(id);
    setNewSession("");
    setMessages([]);
  };

  return (
    <div>
      <h1>对话 / 任务</h1>
      <p className="subtitle">提交任务，查看每步治理裁决与工具结果。多轮对话按 session 维持上下文。</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
          {sessions.length === 0 && <option value="s1">s1</option>}
          {sessions.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.session_id} ({s.msg_count})
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="新 session id"
          value={newSession}
          onChange={(e) => setNewSession(e.target.value)}
        />
        <button className="secondary" onClick={createSession}>
          新建
        </button>
        <input
          type="text"
          placeholder="scenario (可选)"
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          style={{ marginLeft: "auto", width: 180 }}
        />
      </div>

      <div style={{ minHeight: 200, marginBottom: 14 }}>
        {messages.length === 0 && <p className="muted">还没有消息。</p>}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="role">{m.role}</div>
            <div>{m.content}</div>
          </div>
        ))}
      </div>

      {lastResult && (
        <div className="card">
          <div className="row">
            <strong>任务 {lastResult.task_id}</strong>
            <span className={`badge ${stateClass(lastResult.state)}`}>{lastResult.state}</span>
            {lastResult.approval_id && (
              <span className="badge warn">需审批: {lastResult.approval_id}</span>
            )}
          </div>
          {lastResult.steps && lastResult.steps.length > 0 && (
            <div className="steps">
              {lastResult.steps.map((s, i) => (
                <div key={i} className="step-line">
                  {i + 1}. {s.tool} {JSON.stringify(s.args)}
                  <span className={`badge ${verdictClass(s.verdict)}`}>{s.verdict}</span>
                  {s.output && <div className="muted">→ {s.output}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <div className="row">
        <textarea
          placeholder="输入任务…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={send} disabled={loading || !prompt.trim()}>
          {loading ? "执行中…" : "发送 (⌘↵)"}
        </button>
        <span className="muted">每步工具调用都经治理 permit 校验</span>
      </div>
    </div>
  );
}

function stateClass(s: string) {
  if (s === "COMPLETED") return "ok";
  if (s === "NEEDS_REVIEW") return "warn";
  if (s === "REJECTED" || s === "FAILED") return "danger";
  return "";
}
function verdictClass(v: string) {
  if (v.includes("ALLOW")) return "ok";
  if (v.includes("DENY") || v.includes("REJECT")) return "danger";
  if (v.includes("NEEDS") || v.includes("REVIEW")) return "warn";
  return "";
}
