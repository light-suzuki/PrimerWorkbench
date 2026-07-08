import React from "react";

type Props = {
  title?: string;
  children: React.ReactNode;
};

type State = {
  error: Error | null;
};

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error): void {
    // eslint-disable-next-line no-console
    console.error(error);
  }

  render(): React.ReactNode {
    const error = this.state.error;
    if (!error) return this.props.children;

    return (
      <div className="panel-card">
        <h2 className="panel-title">{this.props.title || "UI エラー"}</h2>
        <p className="panel-hint">
          画面描画中にエラーが発生しました。リロードで直ることがあります。
        </p>
        <div style={{ marginTop: 8, display: "flex", gap: "0.45rem", flexWrap: "wrap", alignItems: "center" }}>
          <button type="button" className="seq-button" onClick={() => window.location.reload()}>
            リロード
          </button>
          <button type="button" className="seq-button secondary" onClick={() => this.setState({ error: null })}>
            いったん閉じる
          </button>
        </div>
        <details className="ui-details" style={{ marginTop: 10 }}>
          <summary>詳細</summary>
          <div className="ui-details-body">
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "var(--mono)" }}>
              {error.stack || error.message}
            </pre>
          </div>
        </details>
      </div>
    );
  }
}
