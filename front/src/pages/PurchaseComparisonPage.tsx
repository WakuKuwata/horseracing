import { useState } from "react";

import { usePurchaseComparison } from "../api/queries";
import type { ComparisonPoint, PurchaseComparisonResponse } from "../api/types";
import { PseudoValue } from "../components/PseudoValue";
import { QueryStateView } from "../components/StateView";
import { formatDateTime } from "../lib/format";

type ComparisonScope = "all" | "win_only";

type ComparisonRow = {
  raceId: string;
  raceDate: string;
  actual?: ComparisonPoint;
  policy?: ComparisonPoint;
};

const NOTE_COPY = {
  counterfactual_snapshot:
    "政策線は記録時に凍結した提示スナップショットによる反実仮想の算出値であり、実現値ではありません",
  pre_tax: "金額は税引前です",
  asymmetric_scope:
    "実購入は全券種、政策線は単勝のみの非対称な比較です(単勝のみ表示で対称になります)",
  coverage_denominator_all_races:
    "記録率の分母は期間内の全開催レースです",
} as const;

const PERMANENT_NOTE_KEYS = Object.keys(NOTE_COPY) as Array<keyof typeof NOTE_COPY>;
const numberFormatter = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 2 });

function formatInputDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultDateRange(): { from: string; to: string } {
  const today = new Date();
  const to = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const from = new Date(to);
  from.setDate(from.getDate() - 29);
  return { from: formatInputDate(from), to: formatInputDate(to) };
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return numberFormatter.format(Object.is(value, -0) ? 0 : value);
}

function formatCurrency(value: number | null | undefined): string {
  const formatted = formatNumber(value);
  return formatted === "--" ? formatted : `¥${formatted}`;
}

function formatSignedYen(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  const normalized = Object.is(value, -0) ? 0 : value;
  const sign = normalized > 0 ? "+" : "";
  return `${sign}${numberFormatter.format(normalized)}円`;
}

function formatCoverage(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "開催なし";
  return `${(value * 100).toFixed(1)}%`;
}

function buildRows(data: PurchaseComparisonResponse): ComparisonRow[] {
  const rows = new Map<string, ComparisonRow>();

  for (const actual of data.series.actual) {
    rows.set(actual.race_id, {
      raceId: actual.race_id,
      raceDate: actual.race_date,
      actual,
    });
  }
  for (const policy of data.series.policy) {
    const current = rows.get(policy.race_id);
    rows.set(policy.race_id, {
      raceId: policy.race_id,
      raceDate: current?.raceDate ?? policy.race_date,
      actual: current?.actual,
      policy,
    });
  }

  return [...rows.values()].sort(
    (left, right) =>
      left.raceDate.localeCompare(right.raceDate) ||
      left.raceId.localeCompare(right.raceId),
  );
}

function PolicyValue({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return (
      <>
        算出不能
        <br />
        <small>提示スナップショット無し</small>
      </>
    );
  }
  return <>{formatNumber(value)}</>;
}

function EstimatedSettlementFact({ data }: { data: PurchaseComparisonResponse }) {
  const copy = `うち推定精算 ${formatNumber(data.n_estimated_settlements)} 件(${formatCurrency(
    data.estimated_amount_yen,
  )})`;

  return (
    <div data-testid="estimated-settlements">
      {data.n_estimated_settlements > 0 ? (
        <PseudoValue kind="double_pseudo">{copy}</PseudoValue>
      ) : (
        copy
      )}
    </div>
  );
}

function ComparisonContents({ data }: { data: PurchaseComparisonResponse }) {
  const rows = buildRows(data);

  return (
    <>
      <section className="panel" aria-labelledby="comparison-table-heading">
        <h2 id="comparison-table-heading">レース別の三者比較</h2>
        <div className="table-scroll">
          <table className="data-table" aria-label="購入結果の三者比較">
            <thead>
              <tr>
                <th scope="col">レース</th>
                <th scope="col" className="num">実購入(円)</th>
                <th scope="col" className="num">cap政策・反実仮想(円)</th>
                <th scope="col" className="num">賭けない(常に 0)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.raceId} data-testid={`comparison-row-${row.raceId}`}>
                  <th scope="row">
                    {row.raceDate}
                    <br />
                    <small>{row.raceId}</small>
                  </th>
                  <td className="num">{formatNumber(row.actual?.net_yen)}</td>
                  <td className="num"><PolicyValue value={row.policy?.net_yen} /></td>
                  <td className="num">{formatNumber(data.series.no_bet)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr data-testid="comparison-totals">
                <th scope="row">累積</th>
                <td className="num">{formatNumber(data.cumulative.actual)}</td>
                <td className="num">{formatNumber(data.cumulative.policy)}</td>
                <td className="num">{formatNumber(data.cumulative.no_bet)}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <dl className="backtest-stats" data-testid="cumulative-differences">
          <div>
            <dt>実購入−政策</dt>
            <dd>
              {data.cumulative.diff_actual_vs_policy === null ||
              data.cumulative.diff_actual_vs_policy === undefined ||
              !Number.isFinite(data.cumulative.diff_actual_vs_policy)
                ? "政策線が算出できないため比較不能"
                : formatSignedYen(data.cumulative.diff_actual_vs_policy)}
            </dd>
          </div>
          <div>
            <dt>実購入−賭けない</dt>
            <dd>{formatSignedYen(data.cumulative.diff_actual_vs_no_bet)}</dd>
          </div>
        </dl>
      </section>

      <section className="panel" aria-labelledby="pending-heading">
        <h2 id="pending-heading">未確定の別掲</h2>
        <p data-testid="pending-summary">
          未確定 {formatNumber(data.pending.n_races)}レース・
          {formatNumber(data.pending.n_bets)}点・
          {formatCurrency(data.pending.amount_yen)}(集計に含まれていません)
        </p>
      </section>

      <section className="panel" aria-labelledby="coverage-heading">
        <h2 id="coverage-heading">記録率</h2>
        <div className="audit" data-testid="coverage-facts">
          <div className="stat">
            <span className="stat__k">全体</span>
            <span className="stat__v">{formatCoverage(data.coverage_rate.overall)}</span>
          </div>
          <div className="stat">
            <span className="stat__k">結果取込前に記録</span>
            <span className="stat__v">{formatCoverage(data.coverage_rate.pre_ingestion)}</span>
          </div>
          <div className="stat">
            <span className="stat__k">結果取込後に記録</span>
            <span className="stat__v">{formatCoverage(data.coverage_rate.post_ingestion)}</span>
          </div>
          <div className="stat">
            <span className="stat__k">記録レース / 全開催レース</span>
            <span className="stat__v">
              {formatNumber(data.coverage_rate.n_recorded_races)} / {formatNumber(data.coverage_rate.n_all_races)}
            </span>
          </div>
          <div className="stat">
            <span className="stat__k">訂正</span>
            <span className="stat__v">{formatNumber(data.n_corrections)} 件</span>
          </div>
          <div className="stat">
            <span className="stat__k">提示不明</span>
            <span className="stat__v">{formatNumber(data.n_presentation_unavailable)} 件</span>
          </div>
        </div>
        <p className="note">
          見送りも含め未記録のレースがあると、実購入の線は実態より良く見えうることがあります。
          全開催レースを基準にするため、記録率が低く出るのは正常です。
        </p>
      </section>

      <section className="panel" aria-labelledby="disclosures-heading">
        <h2 id="disclosures-heading">算出条件と注記</h2>
        <ul data-testid="permanent-disclosures">
          {PERMANENT_NOTE_KEYS.map((key) => (
            <li key={key}>{NOTE_COPY[key]}</li>
          ))}
        </ul>
        <EstimatedSettlementFact data={data} />
        <p>
          算出時点: <time dateTime={data.as_of}>{formatDateTime(data.as_of)}</time>
        </p>
        <p className="note">
          実配当が後から取り込まれると推定精算が実精算へ置き換わるため、表示値は正当に変わることがあります。
        </p>
        <small>推定器の出所: {data.estimator_provenance || "--"}</small>
      </section>
    </>
  );
}

export function PurchaseComparisonPage() {
  const [range, setRange] = useState(defaultDateRange);
  const [scope, setScope] = useState<ComparisonScope>("all");
  const [includePostHoc, setIncludePostHoc] = useState(true);
  const query = usePurchaseComparison({
    from: range.from,
    to: range.to,
    scope,
    include_post_hoc: includePostHoc,
  });

  return (
    <main className="page">
      <section className="panel" aria-labelledby="purchase-comparison-heading">
        <h1 id="purchase-comparison-heading">実購入・政策・賭けないの比較</h1>
        <div className="toolbar">
          <label htmlFor="comparison-from">開始日</label>
          <input
            id="comparison-from"
            type="date"
            value={range.from}
            onChange={(event) => setRange((current) => ({ ...current, from: event.target.value }))}
          />
          <label htmlFor="comparison-to">終了日</label>
          <input
            id="comparison-to"
            type="date"
            value={range.to}
            onChange={(event) => setRange((current) => ({ ...current, to: event.target.value }))}
          />
        </div>

        <fieldset>
          <legend>券種スコープ</legend>
          <label>
            <input
              type="radio"
              name="comparison-scope"
              value="all"
              checked={scope === "all"}
              onChange={() => setScope("all")}
            />
            全券種
          </label>{" "}
          <label>
            <input
              type="radio"
              name="comparison-scope"
              value="win_only"
              checked={scope === "win_only"}
              onChange={() => setScope("win_only")}
            />
            単勝のみ=対称ビュー
          </label>
        </fieldset>

        <label>
          <input
            type="checkbox"
            checked={includePostHoc}
            onChange={(event) => setIncludePostHoc(event.target.checked)}
          />
          事後入力を含む（{formatNumber(query.data?.n_post_hoc)}件）
        </label>
      </section>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(data) => data.n_races === 0}
        loadingLabel="購入比較を読み込み中…"
        emptyMessage="この期間の記録はありません"
      >
        {(data) => <ComparisonContents data={data} />}
      </QueryStateView>
    </main>
  );
}
