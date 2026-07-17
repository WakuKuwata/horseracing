import { useShadowLog } from "../api/queries";
import { formatPct } from "../lib/format";
import { QueryStateView } from "./StateView";

/**
 * Feature 075: prospective shadow-betting log — the HONEST instrument. Shows observed outcomes and
 * counterfactual returns for bets recorded BEFORE each race using frozen, actually-bettable odds
 * (NOT closing). Kept in its OWN panel, fully separate from the retrospective backtest in
 * RecommendationPanel, so the two are never conflated. Permanent honest labelling; empty state is
 * shown truthfully (the instrument fills going-forward). No profit language, no P/L coloring or
 * sorting.
 */
export function ShadowLogPanel() {
  const query = useShadowLog();
  return (
    <div className="panel" data-testid="shadow-log-panel">
      <h2>前向き実績(prospective shadow-log)</h2>
      <p className="note" data-testid="shadow-log-labels">
        発走前に記録した買い目を、その時点で<strong>実際に約定できたオッズ(凍結)</strong>で事後精算した
        <strong>反実仮想(判断時オッズ)</strong>の<strong>前向き(prospective)</strong>集計です。
        closing(確定)オッズの事後集計ではなく、
        将来の的中・利益を約束するものではありません。計器はこれから貯まります。
      </p>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => (d?.n_prospective ?? 0) === 0}
        loadingLabel="前向き実績を読み込み中…"
        emptyMessage="まだ前向きデータがありません(収集はこれから)。"
      >
        {(d) => (
          <>
            <dl className="backtest-stats" data-testid="shadow-log-stats">
              <div><dt>確定件数</dt><dd>{d.n_settled}</dd></div>
              <div><dt>的中</dt><dd>{d.n_hit}</dd></div>
              <div><dt>的中率</dt><dd>{d.hit_rate === null || d.hit_rate === undefined ? "—" : formatPct(d.hit_rate)}</dd></div>
              <div>
                <dt>反実仮想(判断時オッズ)回収率(平均回収倍率)</dt>
                <dd>
                  {d.counterfactual_snapshot_recovery_rate === null ||
                    d.counterfactual_snapshot_recovery_rate === undefined
                    ? "—"
                    : `×${d.counterfactual_snapshot_recovery_rate.toFixed(2)}`}
                </dd>
              </div>
              <div><dt>集計待ち(未確定)</dt><dd>{d.n_pending}</dd></div>
              <div><dt>無効(void)</dt><dd>{d.n_void}</dd></div>
              <div><dt>発走前保証が弱い</dt><dd>{d.weak_pretime}</dd></div>
            </dl>
            {d.by_month.length > 0 ? (
              <table className="oddsband-table" data-testid="shadow-log-by-month">
                <thead>
                  <tr><th>月</th><th className="num">確定</th><th className="num">回収</th></tr>
                </thead>
                <tbody>
                  {d.by_month.map((m) => (
                    <tr key={m.month}>
                      <td>{m.month}</td>
                      <td className="num">{m.n_settled}</td>
                      <td className="num">
                        {m.counterfactual_snapshot_recovery === null ||
                          m.counterfactual_snapshot_recovery === undefined
                          ? "—"
                          : `×${m.counterfactual_snapshot_recovery.toFixed(2)}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </>
        )}
      </QueryStateView>
    </div>
  );
}
