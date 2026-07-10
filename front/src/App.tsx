import { Link, Outlet } from "react-router-dom";

export default function App() {
  return (
    <div className="app">
      <header className="app__header">
        <h1>
          <Link to="/">RaceFront</Link>
        </h1>
        <nav className="app__nav">
          <Link to="/">レース</Link>
          <Link to="/shadow-log">前向き実績(shadow-log)</Link>
        </nav>
        <p>競馬予測サーバ(014 API)の読み取り専用ビュー — 推定/疑似値は必ずバッジ表示</p>
      </header>
      <Outlet />
    </div>
  );
}
