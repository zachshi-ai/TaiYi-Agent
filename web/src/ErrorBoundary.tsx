import { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

// Catches render-time exceptions so a single bad component shows a readable
// message instead of unmounting the whole app to a blank white screen.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
          <h1>页面出错了</h1>
          <p className="muted">界面渲染时抛出异常，已被捕获（不再白屏）。</p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              background: "#2a1a1a",
              color: "#ffb4b4",
              padding: 12,
              borderRadius: 8,
              overflow: "auto",
            }}
          >
            {this.state.error.name}: {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack}
          </pre>
          <button onClick={() => this.setState({ error: null })}>重试</button>{" "}
          <button className="secondary" onClick={() => location.reload()}>
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
