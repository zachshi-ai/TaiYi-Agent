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
  operating_mode: "quality" | "balanced" | "efficiency";
  execution_environment: "mock" | "workspace" | "custom" | "unknown";
  policy?: { verification_depth?: string; max_validation_rounds?: number };
  provider_route?: { model?: string | null; provider?: string; fallback?: boolean };
  contract?: {
    contract_id: string;
    checklist_id: string;
    task_type: string;
    task_parameters: Record<string, string>;
    validation_required: boolean;
    objective_evidence_required: boolean;
    objective_covered: boolean;
    coverage: "objective" | "baseline_only";
    acceptance_criteria: {
      id: string;
      description: string;
      evidence_kind: string;
      scope: "baseline" | "objective";
      authority: string;
      environment: string;
      configuration_digest: string;
    }[];
  };
  evidence?: {
    records?: {
      criterion_id: string;
      outcome: string;
      source: string;
      subject_digest: string;
      contract_id: string;
      authority: string;
      environment: string;
      configuration_digest: string;
    }[];
  };
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
  const [operatingMode, setOperatingMode] = useState<"quality" | "balanced" | "efficiency">("balanced");
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
    api.getConfig()
      .then((c) => {
        if (["quality", "balanced", "efficiency"].includes(c.operating_mode)) {
          setOperatingMode(c.operating_mode);
        }
      })
      .catch(() => undefined);
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
      const res: TaskResult = await api.submitTask(
        prompt,
        scenario || undefined,
        sessionId,
        operatingMode,
      );
      setLastResult(res);
      setMessages((m) => [...m, { role: "user", content: prompt }]);
      if (["COMPLETED", "SIMULATED", "NEEDS_INPUT", "CAPABILITY_UNAVAILABLE"].includes(res.state) && res.final_output) {
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

  const contractStatus = lastResult ? acceptanceStatus(lastResult) : null;

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
            <span className="badge">{modeLabel(lastResult.operating_mode)}</span>
            {lastResult.policy?.verification_depth && (
              <span className="badge">验证: {lastResult.policy.verification_depth}</span>
            )}
            {lastResult.provider_route && (
              <span className={`badge ${lastResult.provider_route.fallback ? "warn" : "ok"}`}>
                模型: {lastResult.provider_route.model || lastResult.provider_route.provider}
                {lastResult.provider_route.fallback ? "（回退）" : ""}
              </span>
            )}
            {lastResult.approval_id && (
              <span className="badge warn">需审批: {lastResult.approval_id}</span>
            )}
            <span className={`badge ${lastResult.execution_environment === "mock" ? "warn" : ""}`}>
              {lastResult.execution_environment === "mock" ? "模拟执行" : lastResult.execution_environment}
            </span>
          </div>
          {lastResult.state === "SIMULATED" && (
            <div className="notice" style={{ marginTop: 10 }}>
              本次只完成了无副作用模拟：治理与验收链路已通过，但没有向真实系统交付任何动作。
            </div>
          )}
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
          {contractStatus && (
            <div className="contract-status">
              <div className="row">
                <strong>验收合同</strong>
                <span className="badge">{lastResult.contract?.task_type}</span>
                {lastResult.contract && Object.keys(lastResult.contract.task_parameters || {}).length > 0 && (
                  <span className="mono muted">
                    {JSON.stringify(lastResult.contract.task_parameters)}
                  </span>
                )}
                <span className={`badge ${contractStatus.complete ? "ok" : "warn"}`}>
                  {contractStatus.passed}/{contractStatus.criteria.length} 当前产物通过
                </span>
                <span className="mono muted" title={lastResult.contract?.contract_id}>
                  {shortDigest(lastResult.contract?.contract_id || "")}
                </span>
                {!lastResult.contract?.objective_covered && (
                  <span className="badge warn" title="这些检查不能证明未知任务的目标已经实现">
                    仅基础检查
                  </span>
                )}
              </div>
              <details>
                <summary>查看执行前冻结的验收标准</summary>
                {contractStatus.criteria.map((criterion) => (
                  <div className="criterion-line" key={criterion.id}>
                    <span className={`badge ${criterion.outcome === "PASS" ? "ok" : "warn"}`}>
                      {criterion.outcome}
                    </span>
                    <span>
                      <strong>{criterion.id}</strong> · {criterion.description}
                      <span className="muted">
                        （{criterion.scope === "objective" ? "目标" : "基础"} · {criterion.evidence_kind}
                        · {criterion.authority}/{criterion.environment}）
                      </span>
                    </span>
                  </div>
                ))}
              </details>
            </div>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <div className="mode-switch" role="group" aria-label="Agent 运行模式">
        {([
          ["quality", "质量", "多验证，重要歧义先问"],
          ["balanced", "平衡", "该问就问，该快就快"],
          ["efficiency", "效率", "AI 主导，直达目标"],
        ] as const).map(([value, label, hint]) => (
          <button
            key={value}
            type="button"
            className={operatingMode === value ? "active" : ""}
            onClick={() => setOperatingMode(value)}
            aria-pressed={operatingMode === value}
            aria-label={`${label}模式：${hint}`}
            title={hint}
          >
            <strong>{label}</strong>
            <span>{hint}</span>
          </button>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
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
        <span className="muted">每步工具调用都经治理 permit 校验 · 本任务使用{modeLabel(operatingMode)}</span>
      </div>
    </div>
  );
}

function stateClass(s: string) {
  if (s === "COMPLETED") return "ok";
  if (s === "SIMULATED") return "warn";
  if (s === "NEEDS_REVIEW" || s === "NEEDS_INPUT") return "warn";
  if (s === "REJECTED" || s === "FAILED" || s === "CAPABILITY_UNAVAILABLE") return "danger";
  return "";
}

function modeLabel(mode: string) {
  if (mode === "quality") return "质量模式";
  if (mode === "efficiency") return "效率模式";
  return "平衡模式";
}

function acceptanceStatus(result: TaskResult) {
  const contract = result.contract;
  if (!contract || !contract.validation_required) return null;
  const records = result.evidence?.records || [];
  const currentRecord = [...records]
    .reverse()
    .find((r) => r.contract_id === contract.contract_id);
  const subjectDigest = currentRecord?.subject_digest;
  const criteria = contract.acceptance_criteria.map((criterion) => {
    const evidence = [...records].reverse().find(
      (r) => r.contract_id === contract.contract_id
        && r.subject_digest === subjectDigest
        && r.criterion_id === criterion.id
        && r.source === criterion.evidence_kind
        && r.authority === criterion.authority
        && r.environment === criterion.environment
        && r.configuration_digest === criterion.configuration_digest,
    );
    return { ...criterion, outcome: evidence?.outcome || "PENDING" };
  });
  const passed = criteria.filter((c) => c.outcome === "PASS").length;
  return { criteria, passed, complete: criteria.length > 0 && passed === criteria.length };
}

function shortDigest(digest: string) {
  return digest ? `合同 ${digest.replace(/^sha256:/, "").slice(0, 10)}` : "";
}

function verdictClass(v: string) {
  if (v.includes("ALLOW")) return "ok";
  if (v.includes("DENY") || v.includes("REJECT")) return "danger";
  if (v.includes("NEEDS") || v.includes("REVIEW")) return "warn";
  return "";
}
