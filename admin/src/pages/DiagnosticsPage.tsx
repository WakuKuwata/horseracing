import { useMemo } from "react";

import { useSegmentAccuracy, useSegmentEdge } from "../api/queries";
import type { components } from "../api/schema";
import { LoadingView, ErrorView } from "../components/StateView";
import { formatDateTime, formatInt, formatNum, textOr } from "../lib/format";

/**
 * Feature 054 US2 + Feature 083: diagnostics viewers.
 * Both sections are VERBATIM transcriptions of persisted diagnostic_runs (offline CLI compute →
 * append-only rows → read-only display, 021 discipline) and are SECONDARY (never adoption
 * inputs). The two sections fetch and fail INDEPENDENTLY (codex 083 P0#4): a missing/broken
 * segment-edge run must not hide the segment-accuracy section, and vice versa.
 */
export function DiagnosticsPage() {
  return (
    <div className="panel">
      <SegmentEdgeSection />
      <SegmentAccuracySection />
    </div>
  );
}

/* ---------------------------------- 054: segment edge ---------------------------------- */

function SegmentEdgeSection() {
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
      <section>
        <h1>セグメント診断(モデル p vs 市場 q)</h1>
        <div className="state state--empty" data-code="diagnostic_unavailable">
          <p>永続化された診断がまだありません。オペレータ CLI で実行してください:</p>
          <pre>uv run python -m horseracing_training segment-diagnostic --from 2021-01-01 --persist</pre>
          <p className="note">fold 毎再学習の walk-forward のため数十分かかります(オフライン実行)。</p>
        </div>
      </section>
    );
  }
  if (query.error) return <ErrorView error={query.error} />;
  const data = query.data;
  if (!data) return <LoadingView />;

  return (
    <section>
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
    </section>
  );
}

/* ------------------------------ 083: segment accuracy (082) ----------------------------- */
/*
 * Typed transcription: the API validates the persisted payload against the v1 contract with
 * extra="forbid" and fails closed (typed 409) — no hand-written casts here, generated types only.
 * 082 anti-fishing contract carried into the UI: axes stay in payload (frozen library) order;
 * buckets are shown in FIXED codepoint order (value-independent — JSONB does not preserve
 * object key order, codex P0#2); NO sorting controls, NO worst/rank highlighting, NO profit
 * coloring; every CI is labeled 多重比較未調整; SECONDARY/estimand/confounds never collapse.
 */

type SaRaceAxis = components["schemas"]["SaRaceAxis"];
type SaHorseAxis = components["schemas"]["SaHorseAxis"];
type SaRaceBucket = components["schemas"]["SaRaceBucket"];
type SaHorseBucket = components["schemas"]["SaHorseBucket"];
type SaCIBlock = components["schemas"]["SaCIBlock"];

function fmtCI(ci: SaCIBlock | undefined, digits = 4): string {
  if (!ci || ci.point == null) return "--";
  const p = formatNum(ci.point, digits);
  if (ci.ci_low == null || ci.ci_high == null) return p;
  return `${p} [${formatNum(ci.ci_low, digits)}, ${formatNum(ci.ci_high, digits)}]`;
}

/** FIXED, value-independent bucket order: codepoint ascending (reproduces the producer's
 * sorted() insertion order; JSONB key order is NOT a contract). Never metric-based. */
function orderedBuckets<T>(buckets: Record<string, T>): [string, T][] {
  return Object.entries(buckets).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

function SegmentAccuracySection() {
  const query = useSegmentAccuracy();

  if (query.isLoading) return <LoadingView label="セグメント精度を読み込み中…" />;
  if (query.error?.code === "diagnostic_unavailable") {
    return (
      <section className="sa-section">
        <h1>セグメント精度(検証計器)</h1>
        <div className="state state--empty" data-code="diagnostic_unavailable">
          <p>永続化された segment_accuracy run がまだありません。オペレータ CLI で実行してください:</p>
          <pre>uv run python -m horseracing_training accuracy-readout --eval-from 2019-01-01 --to 2026-07-12 --persist</pre>
        </div>
      </section>
    );
  }
  if (query.error?.code === "diagnostic_contract_unsupported") {
    return (
      <section className="sa-section">
        <h1>セグメント精度(検証計器)</h1>
        <div className="state state--error" data-code="diagnostic_contract_unsupported">
          <p>永続化された run がこのビューアの契約(sa-v1)と一致しません(fail-closed)。</p>
          <p className="note">{query.error.detail}</p>
        </div>
      </section>
    );
  }
  if (query.error) return <ErrorView error={query.error} />;
  const data = query.data;
  if (!data) return <LoadingView />;

  const { instrument_contract: ic, provenance: prov, population: pop, axes } = data.payload;

  return (
    <section className="sa-section">
      <h1>セグメント精度(検証計器・SECONDARY)</h1>
      <p className="note diag-disclaimer">
        {ic.estimand} — <strong>run 生成時の active recipe の歴史的 OOF</strong> であり、現在
        デプロイ中の artifact の運用精度ではありません。採否ゲート・買いシグナルではありません。
        ここでの発見の検証には discovery_run_id 付きの新規事前登録が必要です。全 CI は
        <strong>多重比較未調整</strong>。excess は対 uniform(負 = uniform より正確)。
        citl = mean(p) − 実現率(正 = 過大評価; race grain は構造的に恒等 0 のため非表示)。
      </p>
      <dl className="meta">
        <div><dt>run</dt><dd>{data.diagnostic_run_id}</dd></div>
        <div><dt>計算日時</dt><dd>{formatDateTime(data.computed_at)}</dd></div>
        <div><dt>モデル</dt><dd>{prov.base_model_version}</dd></div>
        <div><dt>評価窓</dt><dd>{textOr(data.date_from)} 〜 {textOr(data.date_to)}</dd></div>
        <div><dt>確率段</dt><dd>{prov.probability_stage}</dd></div>
        <div><dt>bundle</dt><dd>{prov.bundle_digest.slice(0, 12)}</dd></div>
        <div><dt>attestation</dt><dd>{prov.attestation_digest.slice(0, 12)}</dd></div>
        <div><dt>code</dt><dd>{prov.code_sha.slice(0, 12)}</dd></div>
        <div><dt>契約</dt>
          <dd>{ic.metric_contract_version} / {ic.mask_library_version} ({ic.mask_library_hash.slice(0, 12)})</dd></div>
        <div><dt>seed / B</dt><dd>{prov.seed} / {prov.bootstrap_b}</dd></div>
        <div><dt>採点</dt>
          <dd>{formatInt(pop.n_scored_races)} レース / {formatInt(pop.n_scored_horses)} 頭</dd></div>
        <div><dt>除外</dt>
          <dd>{Object.entries(pop.exclusions).map(([k, v]) => `${k}:${v}`).join(" ") || "--"}</dd></div>
      </dl>
      {ic.known_confounds.map((c) => (
        <p key={c} className="note">交絡注記: {c}</p>
      ))}

      {axes.map((axis) => (
        <details key={axis.axis_id} className="sa-axis">
          <summary>
            {axis.axis_id}
            <span className="note"> [{axis.family} / grain={axis.grain}
              {axis.origin === "post_081_exploratory" ? " / 081由来(独立確認には使えない)" : ""}]
            </span>
          </summary>
          {axis.grain === "race"
            ? <RaceAxisTable axis={axis as SaRaceAxis} />
            : <HorseAxisTable axis={axis as SaHorseAxis} />}
        </details>
      ))}
    </section>
  );
}

function RaceAxisTable({ axis }: { axis: SaRaceAxis }) {
  return (
    <table>
      <thead>
        <tr>
          <th>バケット</th>
          <th className="num">n(レース)</th>
          <th className="num">excess(対uniform) [95%CI 未調整]</th>
          <th className="num">対市場(同一母集団)</th>
          <th className="num">ECE [95%CI 未調整]</th>
        </tr>
      </thead>
      <tbody>
        {orderedBuckets<SaRaceBucket>(axis.buckets).map(([name, b]) => (
          <tr key={`${axis.axis_id}:${name}`}>
            <td>{name}</td>
            <td className="num">{formatInt(b.n_races)}</td>
            <td className="num">{fmtCI(b.excess_nll_uniform)}</td>
            <td className="num">
              {b.market.n_market_complete_races > 0
                ? `${formatNum(b.market.excess_nll_market ?? null)} (n=${formatInt(b.market.n_market_complete_races)}/${formatInt(b.market.n_total_races)})`
                : "--"}
            </td>
            <td className="num">{fmtCI(b.ece_ci, 5)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HorseAxisTable({ axis }: { axis: SaHorseAxis }) {
  return (
    <table>
      <thead>
        <tr>
          <th>バケット</th>
          <th className="num">n(頭)</th>
          <th className="num">excess(対uniform) [95%CI 未調整]</th>
          <th className="num">ECE [95%CI 未調整]</th>
          <th className="num">citl</th>
        </tr>
      </thead>
      <tbody>
        {orderedBuckets<SaHorseBucket>(axis.buckets).map(([name, b]) => (
          <tr key={`${axis.axis_id}:${name}`}>
            <td>{name}</td>
            <td className="num">{formatInt(b.n_horses)}</td>
            <td className="num">{fmtCI(b.excess_logloss_vs_uniform)}</td>
            <td className="num">{fmtCI(b.ece_ci, 5)}</td>
            <td className="num">
              {formatNum(b.calibration.calibration_in_the_large ?? null, 5)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
