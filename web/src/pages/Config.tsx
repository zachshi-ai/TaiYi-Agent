import { useEffect, useState } from "react";
import { api, getToken, setToken } from "../api";

export default function Config() {
  const [config, setConfig] = useState<any>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [token, setTokenInput] = useState(getToken());
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const c = await api.getConfig();
      setConfig(c);
      setProvider(c.provider || "offline");
      setModel(c.model || "");
      setMode(c.mode || "agent");
      setApiKeyEnv("");
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setError("");
    setSaved("");
    const updates: Record<string, any> = { provider, mode };
    if (model) updates.model = model;
    if (apiKeyEnv) updates.api_key_env = apiKeyEnv;
    try {
      const r = await api.putConfig(updates);
      setSaved(`已写入 ${r.config_path}。需重启 taiyi 生效。`);
    } catch (e: any) {
      setError(e.message);
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
          </div>
          <p className="muted" style={{ marginTop: 8 }}>
            config_path: {config.config_path || "(无配置文件)"} · base_dir: {config.base_dir || "(内存)"}
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
          <label style={{ marginLeft: 16 }}>provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="offline">offline</option>
            <option value="anthropic">anthropic</option>
            <option value="openai_compat">openai_compat</option>
            <option value="ollama">ollama</option>
          </select>
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          <label>model</label>
          <input type="text" placeholder="模型 id（留空用默认）" value={model} onChange={(e) => setModel(e.target.value)} style={{ flex: 1 }} />
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          <label>api_key_env</label>
          <input type="text" placeholder="环境变量名，如 ANTHROPIC_API_KEY（不存 key 本身）" value={apiKeyEnv} onChange={(e) => setApiKeyEnv(e.target.value)} style={{ flex: 1 }} />
        </div>
        <div className="muted" style={{ marginBottom: 10, fontSize: 12 }}>
          可写字段: {writable.join(", ")}
        </div>
        <button onClick={save}>写回配置</button>
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
        注意：live provider 适配器尚未接线（骨架）。设为非 offline 后重启，首次模型调用会抛 NotImplementedError——这是预期行为，待接入真实 LLM 后即可。
      </div>
    </div>
  );
}
