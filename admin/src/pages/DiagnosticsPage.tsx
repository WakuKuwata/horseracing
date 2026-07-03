import { useMemo } from "react";

import { useSegmentEdge } from "../api/queries";
import { LoadingView, ErrorView } from "../components/StateView";
import { formatDateTime, formatInt, formatNum, textOr } from "../lib/format";

/**
 * Feature 054 US2: segment-edge diagnostics viewer — a VERBATIM transcription of the newest
 * persisted 047 run (offline CLI compute → diagnostic_runs → read-only display, 021 discipline).
 * 047/021 display rules: NO sorting by gap, NO profit coloring, the SECONDARY disclaimer is
 * always visible, and freshness (computed_at / window / logic_version) is always on screen.
 */
export function DiagnosticsPage() {
  const query = useSegmentEdge();

  const byAxis = useMemo(() => {
    const rows = query.data?.rows ?? [];
    const groups = new Map<string, typeof rows>();
    for (const r of rows) {
      const list = groups.get(r.axis) ?? [];
      list.push(r);           // insertion order preserved — the persisted (pre-registered) order
      groups.set(r.axis, list);
    }
    return groups;
  }, [query.data]);

  if (query.isLoading) return <LoadingView label="診断データを読み込み中…" />;
  if (query.error?.code === "diagnostic_unavailable") {
    return (
      <div className="panel">
        <h1>セグメント診断</h1>
        <div className="state state--empty" data-code="diagnostic_unavailable">
          <p>永続化された診断がまだありません。オペレータ CLI で実行してください:</p>
          <pre>uv run python -m horseracing_training segment-diagnostic --from 2021-01-01 --persist</pre>
          <p className="note">fold 毎再学習の walk-forward のため数十分かかります(オフライン実行)。</p>
        </div>
      </div>
    );
  }
  if (query.error) return <ErrorView error={query.error} />;
  const data = query.data;
  if (!data) return <LoadingView />;

  return (
    <div className="panel">
      <h1>セグメント診断(モデル p vs 市場 q)</h1>
      <p className="note diag-disclaimer">
        {data.note} — gap = LL(p) − LL(q)。<strong>正 = その条件では市場の方が正確</strong>。
        採否ゲート・買いシグナルではありません(SECONDARY)。
      </p>
      <dl className="meta">
        <div><dt>計算日時</dt><dd>{formatDateTime(data.computed_at)}</dd></div>
        <div><dt>評価窓</dt>
          <dd>{textOr(data.date_from)} 〜 {textOr(data.date_to)}</dd></div>
        <div><dt>n(頭)</dt><dd>{formatInt(data.n_horses)}</dd></div>
        <div><dt>logic_version</dt><dd>{data.logic_version}</dd></div>
      </dl>

      {[...byAxis.entries()].map(([axis, rows]) => (
        <section key={axis}>
          <h2>{axis}</h2>
          <table>
            <thead>
              <tr>
                <th>セグメント</th>
                <th className="num">n</th>
                <th className="num">勝率</th>
                <th className="num">LL(p)</th>
                <th className="num">LL(q)</th>
                <th className="num">gap</th>
                <th className="num">mean p</th>
                <th className="num">mean q</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.axis}:${r.segment}`}>
                  <td>{r.segment}</td>
                  <td className="num">{formatInt(r.n)}</td>
                  <td className="num">{formatNum(r.win_rate, 4)}</td>
                  <td className="num">{formatNum(r.logloss_p)}</td>
                  <td className="num">{formatNum(r.logloss_q)}</td>
                  <td className="num">{r.gap >= 0 ? "+" : ""}{formatNum(r.gap)}</td>
                  <td className="num">{formatNum(r.mean_p, 4)}</td>
                  <td className="num">{formatNum(r.mean_q, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
