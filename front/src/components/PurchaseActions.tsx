import { useState } from "react";
import type { FormEvent } from "react";

export type PresentedBet = {
  betType: string;
  selection: number[];
  amountYen: number | null;
  oddsUsed: number | null;
};

export type PurchaseRecordKind =
  | "as_presented"
  | "modified"
  | "skipped_presented"
  | "no_recommendation"
  | "presentation_unavailable"
  | "freeform"
  | "correction"
  | "void";

export type PresentedSnapshot = {
  bets: PresentedBet[];
  win_policy: string;
  prediction_run_id: string | null;
  snapshot_schema_version: 1;
};

export type PurchaseRecordPayload = {
  race_id: string;
  kind: PurchaseRecordKind;
  bets: PresentedBet[];
  presented_snapshot: PresentedSnapshot | null;
  client_request_id: string;
  corrects_record_id?: string;
};

export interface SubmitResult {
  recordId?: string;
  record_id?: string;
  [key: string]: unknown;
}

export type PurchaseActionsProps = {
  raceId: string;
  presentedBets: PresentedBet[];
  predictionRunId: string | null;
  winPolicy: string;
  presentationAvailable: boolean;
  hasResults: boolean;
  submit: (payload: PurchaseRecordPayload) => Promise<SubmitResult>;
  existingRecord: { kind: string; recordId: string } | null;
};

type ActionKind = Exclude<PurchaseRecordKind, "correction">;

type EditorRow = {
  bet: PresentedBet;
  amountText: string;
  excluded: boolean;
};

type SubmissionState =
  | { phase: "idle" }
  | { phase: "submitting"; payload: PurchaseRecordPayload; action: ActionKind }
  | { phase: "success"; payload: PurchaseRecordPayload; action: ActionKind }
  | {
      phase: "failure";
      payload: PurchaseRecordPayload;
      action: ActionKind;
      reason: string;
    };

function cloneBet(bet: PresentedBet): PresentedBet {
  return { ...bet, selection: [...bet.selection] };
}

function submissionReason(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return "記録できませんでした。通信状態を確認してください。";
}

function actionSummary(payload: PurchaseRecordPayload): string {
  if (payload.bets.length === 0) return "見送り・0点";
  const total = payload.bets.reduce((sum, bet) => sum + (bet.amountYen ?? 0), 0);
  return `${payload.bets.length}点・合計 ${total.toLocaleString("ja-JP")}円`;
}

export function PurchaseActions({
  raceId,
  presentedBets,
  predictionRunId,
  winPolicy,
  presentationAvailable,
  hasResults,
  submit,
  existingRecord,
}: PurchaseActionsProps) {
  const [editorRows, setEditorRows] = useState<EditorRow[] | null>(null);
  const [submission, setSubmission] = useState<SubmissionState>({ phase: "idle" });

  const isSubmitting = submission.phase === "submitting";
  const hasSucceeded = submission.phase === "success";
  const correctionSuffix = existingRecord ? "を訂正として記録" : "を記録";

  function createPayload(action: ActionKind, bets: PresentedBet[]): PurchaseRecordPayload {
    const isCorrection = existingRecord !== null;
    const snapshot =
      isCorrection || !presentationAvailable
        ? null
        : {
            bets: presentedBets.map(cloneBet),
            win_policy: winPolicy,
            prediction_run_id: predictionRunId,
            snapshot_schema_version: 1 as const,
          };

    return {
      race_id: raceId,
      kind: isCorrection ? "correction" : action,
      bets: bets.map(cloneBet),
      presented_snapshot: snapshot,
      client_request_id: crypto.randomUUID(),
      ...(isCorrection ? { corrects_record_id: existingRecord.recordId } : {}),
    };
  }

  async function send(payload: PurchaseRecordPayload, action: ActionKind) {
    setSubmission({ phase: "submitting", payload, action });
    try {
      await submit(payload);
      setSubmission({ phase: "success", payload, action });
    } catch (error) {
      setSubmission({ phase: "failure", payload, action, reason: submissionReason(error) });
    }
  }

  function recordAsPresented() {
    const bets = presentedBets.filter(
      (bet): bet is PresentedBet & { amountYen: number } =>
        bet.amountYen !== null && bet.amountYen > 0,
    );
    void send(createPayload("as_presented", bets), "as_presented");
  }

  function openModifiedEditor() {
    setEditorRows(
      presentedBets.map((bet) => ({
        bet: cloneBet(bet),
        amountText: bet.amountYen === null ? "" : String(bet.amountYen),
        excluded: false,
      })),
    );
  }

  function skipKind(): ActionKind {
    if (!presentationAvailable) return "presentation_unavailable";
    return presentedBets.length > 0 ? "skipped_presented" : "no_recommendation";
  }

  function recordSkip() {
    const action = skipKind();
    void send(createPayload(action, []), action);
  }

  const includedEditorRows = editorRows?.filter((row) => !row.excluded) ?? [];
  const hasInvalidAmount = includedEditorRows.some((row) => {
    const amount = Number(row.amountText);
    return !Number.isInteger(amount) || amount <= 0 || amount % 100 !== 0;
  });
  const editorError =
    editorRows === null
      ? null
      : includedEditorRows.length === 0
        ? "少なくとも1行を記録対象にしてください。"
        : hasInvalidAmount
          ? "記録対象の金額は100円以上、100円単位で入力してください。"
          : null;

  function submitModified(event: FormEvent) {
    event.preventDefault();
    if (editorRows === null || editorError !== null) return;

    const bets = editorRows
      .filter((row) => !row.excluded)
      .map((row) => ({ ...cloneBet(row.bet), amountYen: Number(row.amountText) }));
    void send(createPayload("modified", bets), "modified");
  }

  function updateEditorRow(index: number, update: Partial<Pick<EditorRow, "amountText" | "excluded">>) {
    setEditorRows((rows) =>
      rows?.map((row, rowIndex) => (rowIndex === index ? { ...row, ...update } : row)) ?? null,
    );
  }

  function retry() {
    if (submission.phase !== "failure") return;
    void send(submission.payload, submission.action);
  }

  return (
    <section className="purchase-actions" aria-label="購入内容の記録">
      <p className="note">実際の対応内容を記録できます。見送りも同じ記録操作です。</p>
      {hasResults ? (
        <p className="note" data-testid="post-result-note">
          結果取込後の記録(事後入力)として保存されます
        </p>
      ) : null}

      <div className="purchase-actions__buttons">
        <button
          className="purchase-actions__button"
          type="button"
          onClick={recordAsPresented}
          disabled={isSubmitting || hasSucceeded}
        >
          そのまま購入{correctionSuffix}
        </button>
        <button
          className="purchase-actions__button"
          type="button"
          onClick={openModifiedEditor}
          disabled={isSubmitting || hasSucceeded}
        >
          変更して購入{correctionSuffix}
        </button>
        <button
          className="purchase-actions__button"
          type="button"
          onClick={recordSkip}
          disabled={isSubmitting || hasSucceeded}
        >
          見送り{correctionSuffix}
        </button>
      </div>

      {editorRows !== null ? (
        <form className="purchase-actions__editor" onSubmit={submitModified}>
          <p className="note">記録する金額を編集し、記録しない行は除外してください。</p>
          {editorRows.map((row, index) => (
            <div className="purchase-actions__editor-row" key={`${row.bet.betType}-${index}`}>
              <span>
                {row.bet.betType} {row.bet.selection.join("-")}
              </span>
              <label htmlFor={`purchase-amount-${index}`}>金額</label>
              <input
                id={`purchase-amount-${index}`}
                data-testid={`modified-amount-${index}`}
                type="number"
                inputMode="numeric"
                min={100}
                step={100}
                value={row.amountText}
                disabled={row.excluded || isSubmitting || hasSucceeded}
                onChange={(event) => updateEditorRow(index, { amountText: event.target.value })}
              />
              <label>
                <input
                  data-testid={`modified-exclude-${index}`}
                  type="checkbox"
                  checked={row.excluded}
                  disabled={isSubmitting || hasSucceeded}
                  onChange={(event) => updateEditorRow(index, { excluded: event.target.checked })}
                />
                この行を除外
              </label>
            </div>
          ))}
          {editorError ? (
            <p className="note" role="alert" data-testid="modified-error">
              {editorError}
            </p>
          ) : null}
          <button type="submit" disabled={editorError !== null || isSubmitting || hasSucceeded}>
            変更内容{correctionSuffix}
          </button>
        </form>
      ) : null}

      {submission.phase === "submitting" ? (
        <p className="note" role="status">
          送信中です
        </p>
      ) : null}
      {submission.phase === "success" ? (
        <p className="note" role="status" data-testid="purchase-record-success">
          記録しました: {actionSummary(submission.payload)}
        </p>
      ) : null}
      {submission.phase === "failure" ? (
        <div className="purchase-actions__failure" role="alert">
          <p className="note">記録できませんでした: {submission.reason}</p>
          <button type="button" onClick={retry}>
            同じ内容で再試行
          </button>
        </div>
      ) : null}
    </section>
  );
}
