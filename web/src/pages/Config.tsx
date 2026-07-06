import { useEffect, useState } from "react";
import { api, getToken, setToken } from "../api";

// Ollama is a local service — it needs no API key, so hide the key field for it.
const NO_KEY_PROVIDERS = new Set(["offline", "ollama"]);

export default function Config() {
  const [config, setConfig] = useState<any>(null);
  const [provider, setProvider] = useState("offline");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [mode, setMode] = useState("agent");
  const [token, setTokenInput] = useState(getToken());
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);

  const load = async () => {
    try {
      const c = await api.getConfig();
      setConfig(c);
      setProvider(c.provider?.replace(/^live:/, "") || "offline");
      setModel(c.model || "");
      setBaseUrl(c.base_url || "");
      setMode(c.mode || "agent");
      setApiKey(""); // never echo the stored value; user re-types to change
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const needsKey = !NO_KEY_PROVIDERS.has(provider);

  const buildUpdates = () => {
    const updates: Record<string, any> = { provider, mode };
    if (model) updates.model = model;
    if (baseUrl) updates.base_url = baseUrl;
    if (needsKey) {
      // empty string signals "clear the key"; only send if user typed something
      updates.api_key = apiKey || "";
    }
    return updates;
  };

  const save = async () => {
    setError("");
    setSaved("");
    setTestResult(null);
    try {
      const r = await api.putConfig(buildUpdates());
      setSaved(`已写入 ${r.config_path}。需重启 taiyi 生效。`);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const test = async () => {
    setError("");
    setSaved("");
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testConfig({
        provider,
        base_url: baseUrl,
        model: model || null,
        api_key: needsKey ? apiKey : null,
      });
      setTestResult(r);
    } catch (e: any) {
      setTestResult({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const saveToken = () => {
    setToken(token);
    setSaved("鉴权 token 已保存到浏览器。");
  };

  const writable = config?.writable_fields || [];

  return (
    <div>
      <h1>配置</h1>
      <p className="subtitle">
        修改配置写回 taiyi.yaml，需重启生效（治理与技能集只读加载，运行时不热切换）。
      </p>
      {error && <div className="error">{error}</div>}
      {saved && <div className="notice">{saved}</div>}

      {config && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>当前运行状态</h2>
          <div className="mono">
            mode: <span className="badge">{config.mode}</span>　
            provider: <span className="badge">{config.provider}</span>　
            model: <span className="badge">{config.model || "默认"}</span>　
            {config.base_url && <span>base_url: {config.base_url}</span>}
          </div>
          <p className="muted" style={{ marginTop: 8 }}>
            config_path: {config.config_path || "(无配置文件)"} · base_dir: {config.base_dir || "(内存)"}
            {config.api_key_set != null && ` · api_key: ${config.api_key_set ? "已设置" : "未设置"}`}
          </p>
        </div>
      )}

      <div className="card">
        <h2 style={{ marginTop: 0 }}>LLM 配置</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <label>mode</label>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="agent">agent (ReAct)</option>
            <option value="workflow">workflow (plan-once)</option>
          </select>
          <label style={{ marginLeft: 16 }}>协议</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="offline">offline（离线，零 token）</option>
            <option value="ollama">ollama（本地，无需 key）</option>
            <option value="openai_compat">openai_compat（DeepSeek/智谱/OpenAI…）</option>
          </select>
        </div>

        {provider !== "offline" && (
          <>
            <div className="row" style={{ marginBottom: 10 }}>
              <label>baseURL</label>
              <input
                type="text"
                placeholder={provider === "ollama" ? "http://localhost:11434/v1" : "https://api.deepseek.com/v1"}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>
            <div className="row" style={{ marginBottom: 10 }}>
              <label>model</label>
              <input
                type="text"
                placeholder={provider === "ollama" ? "qwen2.5:7b" : "deepseek-chat"}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>
            {needsKey && (
              <div className="row" style={{ marginBottom: 10 }}>
                <label>apiKey</label>
                <input
                  type="password"
                  placeholder={config?.api_key_set ? "（已设置，留空保持不变）" : "sk-..."}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  style={{ flex: 1 }}
                />
              </div>
            )}
            {provider === "ollama" && (
              <p className="muted" style={{ fontSize: 12, margin: "0 0 10px" }}>
                Ollama 是本地服务，无需 API key。先确保已 <code>ollama serve</code> 并 <code>ollama pull &lt;模型&gt;</code>。
              </p>
            )}
          </>
        )}

        <div className="muted" style={{ marginBottom: 10, fontSize: 12 }}>
          可写字段: {writable.join(", ")}
        </div>
        <div className="row gap">
          <button onClick={save}>保存配置（写回 taiyi.yaml）</button>
          {provider !== "offline" && (
            <button className="secondary" onClick={test} disabled={testing || !baseUrl}>
              {testing ? "测试中…" : "测试连接"}
            </button>
          )}
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          不会自动保存——改完点「保存配置」写回文件，再重启 taiyi 生效。
        </p>
        {saved && <div className="notice" style={{ marginTop: 10 }}>{saved}</div>}
        {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}
        {testResult && (
          <div className={testResult.ok ? "notice" : "error"} style={{ marginTop: 10 }}>
            {testResult.ok
              ? `✓ 连接成功${testResult.model ? `（模型: ${testResult.model}）` : ""}${testResult.reply ? ` · 回复: ${testResult.reply}` : ""}`
              : `✗ ${testResult.error}`}
          </div>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>鉴权</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <label>Bearer token</label>
          <input type="password" placeholder="（无 auth 时留空）" value={token} onChange={(e) => setTokenInput(e.target.value)} style={{ flex: 1 }} />
          <button className="secondary" onClick={saveToken}>保存到浏览器</button>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>仅存于浏览器 localStorage，用于调用 API 时的 Authorization 头。</p>
      </div>

      <div className="notice">
        需要 live 依赖：首次使用前请 <code>pip install -e ".[live]"</code>（装 httpx）。
        配置写回后重启 taiyi 生效——治理与技能集只在启动时加载，运行时不热切换。
      </div>
    </div>
  );
}
