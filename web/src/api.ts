// Thin fetch wrapper for the taiyi gateway. Same-origin in production (the
// gateway serves this UI), so no base URL or CORS needed. Auth token is read
// from localStorage so a user can set it once and have it apply to all calls.

export function getToken(): string {
  return localStorage.getItem("taiyi_token") || "";
}

export function setToken(t: string) {
  if (t) localStorage.setItem("taiyi_token", t);
  else localStorage.removeItem("taiyi_token");
}

async function req(path: string, opts: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(path, { ...opts, headers });
  const text = await resp.text();
  let body: any = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* keep raw text (e.g. /metrics) */
  }
  if (!resp.ok) {
    const msg = body?.error || resp.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

export const api = {
  // tasks / chat
  submitTask: (
    prompt: string,
    scenario?: string,
    sessionId?: string,
    operatingMode: "quality" | "balanced" | "efficiency" = "balanced",
  ) =>
    req("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        prompt,
        scenario,
        session_id: sessionId,
        operating_mode: operatingMode,
      }),
    }),

  // approvals
  listApprovals: () => req("/v1/approvals"),
  resolveApproval: (approvalId: string, decision: "approve" | "reject") =>
    req("/v1/approvals/resolve", {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId, decision }),
    }),

  // OODA review
  listPending: () => req("/v1/review/pending"),
  resolveReview: (id: number, action: "approve" | "reject") =>
    req(`/v1/review/${id}/${action}`, { method: "POST", body: "{}" }),
  trajectories: () => req("/v1/trajectories"),
  report: () => req("/v1/report"),

  // observe
  sessions: () => req("/v1/sessions"),
  sessionMessages: (id: string) => req(`/v1/sessions/${id}/messages`),
  memories: () => req("/v1/memories"),
  skills: () => req("/v1/skills"),
  metricsJson: () => req("/v1/metrics.json"),

  // config
  getConfig: () => req("/v1/config"),
  putConfig: (updates: Record<string, any>) =>
    req("/v1/config", { method: "PUT", body: JSON.stringify(updates) }),
  testConfig: (cfg: Record<string, any>) =>
    req("/v1/config/test", { method: "POST", body: JSON.stringify(cfg) }),
};
