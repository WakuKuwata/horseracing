import { Link, Outlet } from "react-router-dom";

/**
 * Feature 051: admin console shell. LOCALHOST-ONLY operational tool (auth deferred — the header
 * banner keeps that fact visible). Read-only in this feature: model registry + detail.
 */
export function App() {
  return (
    <div className="app">
      <header className="app__header">
        <Link to="/" className="app__brand">運用コンソール</Link>
        <nav className="app__nav">
          <Link to="/">モデル</Link>
          <Link to="/coverage">被覆率</Link>
          <Link to="/jobs">ジョブ</Link>
        </nav>
        <span className="app__banner">localhost 専用・認証なし(公開禁止)</span>
      </header>
      <main className="app__main">
        <Outlet />
      </main>
    </div>
  );
}
