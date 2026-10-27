import { Link, Outlet } from "react-router-dom";

import { OpsPauseBanner } from "./components/OpsPauseBanner";

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
          <Link to="/diagnostics">診断</Link>
        </nav>
        <span className="app__banner">localhost 専用・認証なし(公開禁止)</span>
        {/* B5: the 172 pause / wrapper mismatch, visible on every admin page */}
        <OpsPauseBanner />
      </header>
      <main className="app__main">
        <Outlet />
      </main>
    </div>
  );
}
