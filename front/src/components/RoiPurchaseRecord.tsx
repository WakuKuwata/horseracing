import { useState } from "react";

import { usePurchaseRecording } from "../api/purchaseRecording";
import { formatYen } from "../lib/budget";
import { formatDateTime } from "../lib/format";
import { RECORD_KIND_LABELS } from "../lib/purchaseLabels";
import { FreeformPurchaseForm, type FreeformPayload } from "./FreeformPurchaseForm";
import { PAUSE_SHORT } from "./OpsPauseNotice";

type SkipState =
  | { phase: "idle" }
  | { phase: "submitting" }
  | { phase: "success" }
  | { phase: "failure"; reason: string };

/**
 * B6 (2026-10-28): record the ACTUAL purchase or skip from the ROI view itself.
 *
 * Feature 106's recording lived under the legacy slip (now the comparison tab), so a user who
 * reads only the ROI table had no way to record what they did — guardrail 6 (judge by real
 * betting behaviour) was cut off from the main path. This section offers exactly the two record
 * kinds that do not need a presented slip: a skip (`presentation_unavailable`, 0 bets — the legacy
 * presentation was not consulted, which is what that kind means) and a free-form purchase. The
 * "as presented / modified / correction" kinds stay in the comparison tab because they freeze the
 * legacy slip as displayed there. It is NOT a recommendation: nothing here suggests a bet.
 */
export function RoiPurchaseRecord({
  raceId,
  raceDate,
  horseNumbers,
  hasResults,
}: {
  raceId: string;
  raceDate: string | undefined;
  horseNumbers: number[];
  hasResults: boolean;
}) {
  const { existingView, submit, paused, isLoading } = usePurchaseRecording(raceId, raceDate);
  const [skip, setSkip] = useState<SkipState>({ phase: "idle" });
  const blocked = existingView !== null || paused || skip.phase === "submitting" || skip.phase === "success";

  async function recordSkip() {
    setSkip({ phase: "submitting" });
    try {
      await submit({
        race_id: raceId,
        kind: "presentation_unavailable",
        bets: [],
        presented_snapshot: null,
        client_request_id: crypto.randomUUID(),
      });
      setSkip({ phase: "success" });
    } catch (error) {
      setSkip({
        phase: "failure",
        reason: error instanceof Error && error.message ? error.message : "記録できませんでした。",
      });
    }
  }

  const total = existingView?.bets.reduce((sum, b) => sum + (b.amount_yen ?? 0), 0) ?? 0;

  return (
    <section className="panel roi-purchase" aria-labelledby="roi-purchase-title" data-testid="roi-purchase-record">
      <h2 id="roi-purchase-title">購入記録(このレース)</h2>
      <p className="note">
        実際に行った購入・見送りをここに記録します。記録は追記のみで、三者比較の実購入線に入ります。
        旧方策の提示に基づく記録(そのまま購入・変更して購入・訂正)は「予測モデルとの比較」タブにあります。
        ここに買い目の提案はありません。
      </p>
      {hasResults ? (
        <p className="note" data-testid="roi-purchase-post-result-note">
          結果取込後の記録(事後入力)として保存されます
        </p>
      ) : null}
      {paused ? (
        <p className="purchase-actions__paused" role="status" data-testid="ops-paused-notice">
          {PAUSE_SHORT}(購入記録の保存も止まっています)
        </p>
      ) : null}

      {existingView ? (
        <p className="roi-purchase__existing" data-testid="roi-purchase-existing">
          記録済み: {RECORD_KIND_LABELS[existingView.kind] ?? existingView.kind}
          {existingView.bets.length > 0
            ? `・${existingView.bets.length}点・合計 ${formatYen(total)}`
            : "・0点"}
          ・{formatDateTime(existingView.recorded_at)}
          {existingView.n_corrections > 0 ? `・訂正 ${existingView.n_corrections} 回` : ""}
          。訂正は「予測モデルとの比較」タブの記録欄から行えます。
        </p>
      ) : isLoading ? (
        <p className="note" data-testid="roi-purchase-loading">記録状態を読み込み中…</p>
      ) : null}

      <div className="purchase-actions__buttons">
        <button
          type="button"
          className="purchase-actions__button"
          onClick={() => void recordSkip()}
          disabled={blocked}
          data-testid="roi-purchase-skip"
        >
          見送りを記録
        </button>
        {skip.phase === "success" ? (
          <span role="status" data-testid="roi-purchase-skip-result">見送りを記録しました</span>
        ) : null}
        {skip.phase === "failure" ? (
          <span role="status" data-testid="roi-purchase-skip-result">
            記録できませんでした: {skip.reason}
            <button type="button" onClick={() => void recordSkip()} disabled={paused}>再試行</button>
          </span>
        ) : null}
      </div>

      <FreeformPurchaseForm
        raceId={raceId}
        horseNumbers={horseNumbers}
        submit={(payload: FreeformPayload) => submit(payload)}
        disabled={blocked}
      />
    </section>
  );
}
