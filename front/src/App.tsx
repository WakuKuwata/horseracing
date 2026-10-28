import { Link, Outlet } from "react-router-dom";

import { OpsPauseBanner } from "./components/OpsPauseBanner";

export default function App() {
  return (
    <div className="app">
      <header className="app__header">
        <h1>
          <Link to="/">RaceFront</Link>
        </h1>
        <nav className="app__nav">
          <Link to="/">レース</Link>
          <Link to="/attention">注目条件</Link>
          <Link to="/market-ev/prospective">期待回収率の前向き現況</Link>
          <Link to="/shadow-log">前向き実績(shadow-log)</Link>
          <Link to="/purchases">購入記録</Link>
          <Link to="/purchase-comparison">三者比較</Link>
        </nav>
        <p>競馬予測サーバ(014 API)の読み取り専用ビュー — 推定/疑似値は必ずバッジ表示</p>
        {/* B5: the 172 pause, once, where every page sees it (buttons repeat it locally). */}
        <OpsPauseBanner />
      </header>
      <Outlet />
    </div>
  );
}
