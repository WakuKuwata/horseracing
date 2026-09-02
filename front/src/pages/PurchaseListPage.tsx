import { useState } from "react";

import { usePurchaseRecords } from "../api/queries";
import type { PurchaseBetView, PurchaseRecordView } from "../api/types";
import { PseudoValue } from "../components/PseudoValue";
import { EmptyView, LoadingView } from "../components/StateView";

const BET_TYPE_LABELS: Record<string, string> = {
  win: "単勝",
  place: "複勝",
  quinella: "馬連",
  exacta: "馬単",
  wide: "ワイド",
  trio: "三連複",
  trifecta: "三連単",
};

const RECORD_KIND_LABELS: Record<string, string> = {
  as_presented: "提示どおり",
  modified: "修正して購入",
  skipped_presented: "見送り(提示あり)",
  no_recommendation: "見送り(推奨なし)",
  presentation_unavailable: "提示不明",
  freeform: "自由入力",
};

function formatDateInput(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultDateRange(): { from: string; to: string } {
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 29);
  return { from: formatDateInput(from), to: formatDateInput(today) };
}

function formatYen(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `¥${new Intl.NumberFormat("ja-JP").format(value)}`
    : "--";
}

function formatSelection(selection: number[] | null | undefined): string {
  if (!Array.isArray(selection) || selection.length === 0) return "--";
  return selection
    .map((number) => (Number.isFinite(number) ? String(number) : "--"))
    .join("-");
}

function displayText(value: string | null | undefined): string {
  return value ? value : "--";
}

function errorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    if ("message" in error && typeof error.message === "string" && error.message) {
      return error.message;
    }
    if ("detail" in error && typeof error.detail === "string" && error.detail) {
      return error.detail;
    }
  }
  return "購入記録を取得できませんでした";
}

function SettlementText({ bet }: { bet: PurchaseBetView }) {
  switch (bet.status) {
    case "pending":
      return <>未確定</>;
    case "settled_real":
      if (bet.hit === true) return <>的中 払戻 {formatYen(bet.payout_yen)}</>;
      if (bet.hit === false) return <>不的中</>;
      return <>--</>;
    case "settled_estimated":
      if (bet.hit === true) {
        return (
          <>
            的中 払戻{" "}
            <PseudoValue kind="double_pseudo">{formatYen(bet.payout_yen)}</PseudoValue>
          </>
        );
      }
      if (bet.hit === false) return <>不的中</>;
      return <>--</>;
    case "refunded":
      return <>返還</>;
    case "unsettleable":
      return <>精算不能(単勝オッズ欠落)</>;
    default:
      return <>--</>;
  }
}

function PurchaseRecordCard({
  record,
  index,
}: {
  record: PurchaseRecordView;
  index: number;
}) {
  const headingId = `purchase-record-${index}`;

  return (
    <article className="panel" aria-labelledby={headingId}>
      <h2 id={headingId}>
        {displayText(record.race_date)} / {displayText(record.race_id)}
      </h2>

      <dl className="race-meta">
        <div>
          <dt>記録種別</dt>
          <dd>{RECORD_KIND_LABELS[record.kind] ?? displayText(record.kind)}</dd>
        </div>
        <div>
          <dt>記録時点</dt>
          <dd>
            <span className="badge badge--history">
              {record.result_pending_at_record
                ? "結果取込前に記録"
                : "結果取込後に記録"}
            </span>
          </dd>
        </div>
        <div>
          <dt>記録日時</dt>
          <dd>{displayText(record.recorded_at)}</dd>
        </div>
        {record.n_corrections > 0 ? (
          <div>
            <dt>訂正</dt>
            <dd>訂正 {record.n_corrections} 回</dd>
          </div>
        ) : null}
        {record.was_voided ? (
          <div>
            <dt>状態</dt>
            <dd><span className="badge badge--history">取消後に再記録</span></dd>
          </div>
        ) : null}
      </dl>

      {record.note !== null && record.note !== undefined ? (
        <p>メモ: {record.note || "--"}</p>
      ) : null}

      {record.anomalies.length > 0 ? (
        <ul className="note" aria-label="確認事項">
          {record.anomalies.map((anomaly, anomalyIndex) => (
            <li key={`${record.record_id}-anomaly-${anomalyIndex}`}>
              {displayText(anomaly)}
            </li>
          ))}
        </ul>
      ) : null}

      {record.bets.length === 0 ? (
        <p className="note">購入明細なし</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>券種</th>
              <th>馬番</th>
              <th className="num">金額</th>
              <th>精算</th>
            </tr>
          </thead>
          <tbody>
            {record.bets.map((bet, betIndex) => (
              <tr key={`${record.record_id}-${bet.bet_type}-${betIndex}`}>
                <td>{BET_TYPE_LABELS[bet.bet_type] ?? displayText(bet.bet_type)}</td>
                <td>{formatSelection(bet.selection)}</td>
                <td className="num">{formatYen(bet.amount_yen)}</td>
                <td><SettlementText bet={bet} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}

export function PurchaseListPage() {
  const [range, setRange] = useState(defaultDateRange);
  const query = usePurchaseRecords(range);

  return (
    <main className="page">
      <section className="panel">
        <h1>購入記録</h1>
        <div className="toolbar">
          <label htmlFor="purchase-from">開始日</label>
          <input
            id="purchase-from"
            type="date"
            value={range.from}
            onChange={(event) =>
              setRange((current) => ({ ...current, from: event.target.value }))
            }
          />
          <label htmlFor="purchase-to">終了日</label>
          <input
            id="purchase-to"
            type="date"
            value={range.to}
            onChange={(event) =>
              setRange((current) => ({ ...current, to: event.target.value }))
            }
          />
        </div>
      </section>

      {query.isLoading || query.data === undefined && !query.error ? (
        <LoadingView label="購入記録を読み込み中…" />
      ) : query.error ? (
        <div className="state state--error" role="alert" data-state="error">
          {errorMessage(query.error)}
        </div>
      ) : query.data.n_races_recorded === 0 ? (
        <EmptyView message="この期間の記録はありません" />
      ) : (
        <section aria-label="購入記録一覧">
          <p className="note">
            結果取込前の記録でも、取込遅延により実際は発走後だった可能性があります。
          </p>
          {query.data.records.map((record, index) => (
            <PurchaseRecordCard
              key={`${record.record_id}-${index}`}
              record={record}
              index={index}
            />
          ))}
        </section>
      )}
    </main>
  );
}
