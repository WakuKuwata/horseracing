import { useRef, useState } from "react";

export type FreeformBet = {
  betType: "win" | "place" | "quinella" | "exacta" | "wide" | "trio" | "trifecta";
  selection: number[];
  amountYen: number;
};

export type FreeformPayload = {
  race_id: string;
  kind: "freeform" | "presentation_unavailable";
  bets: {
    betType: string;
    selection: number[];
    amountYen: number | null;
    oddsUsed: number | null;
  }[];
  presented_snapshot: null;
  client_request_id: string;
};

export type FreeformPurchaseFormProps = {
  raceId: string;
  horseNumbers: number[];
  submit: (payload: FreeformPayload) => Promise<unknown>;
  disabled?: boolean;
};

type BetType = FreeformBet["betType"];
type SelectionValue = number | "";

type EditorRow = {
  id: number;
  betType: BetType;
  selection: SelectionValue[];
  amountText: string;
};

type SubmissionState =
  | { phase: "idle" }
  | { phase: "submitting"; payload: FreeformPayload }
  | { phase: "success"; payload: FreeformPayload }
  | { phase: "failure"; payload: FreeformPayload; reason: string };

const BET_TYPES: {
  value: BetType;
  label: string;
  selectionLabels: string[];
  unordered: boolean;
}[] = [
  { value: "win", label: "単勝", selectionLabels: ["馬番"], unordered: false },
  { value: "place", label: "複勝", selectionLabels: ["馬番"], unordered: false },
  {
    value: "quinella",
    label: "馬連",
    selectionLabels: ["1頭目", "2頭目"],
    unordered: true,
  },
  {
    value: "exacta",
    label: "馬単",
    selectionLabels: ["1着", "2着"],
    unordered: false,
  },
  {
    value: "wide",
    label: "ワイド",
    selectionLabels: ["1頭目", "2頭目"],
    unordered: true,
  },
  {
    value: "trio",
    label: "三連複",
    selectionLabels: ["1頭目", "2頭目", "3頭目"],
    unordered: true,
  },
  {
    value: "trifecta",
    label: "三連単",
    selectionLabels: ["1着", "2着", "3着"],
    unordered: false,
  },
];

function betTypeConfig(betType: BetType) {
  return BET_TYPES.find((candidate) => candidate.value === betType) ?? BET_TYPES[0];
}

function selectionForType(
  betType: BetType,
  horseNumbers: number[],
  current: SelectionValue[] = [],
): SelectionValue[] {
  const count = betTypeConfig(betType).selectionLabels.length;
  const selection = current.slice(0, count);

  while (selection.length < count) {
    const nextHorse = horseNumbers.find((horseNumber) => !selection.includes(horseNumber));
    selection.push(nextHorse ?? "");
  }

  return selection;
}

function submissionReason(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return "記録できませんでした。通信状態を確認してください。";
}

function amountError(amountText: string): string | null {
  const amount = Number(amountText);
  if (!Number.isInteger(amount) || amount <= 0 || amount % 100 !== 0) {
    return "金額は100円以上、100円単位の整数で入力してください。";
  }
  return null;
}

function selectionError(selection: SelectionValue[]): string | null {
  if (selection.some((horseNumber) => horseNumber === "")) {
    return "すべての馬番を選択してください。";
  }
  if (new Set(selection).size !== selection.length) {
    return "同じ馬番を重複して選択することはできません。";
  }
  return null;
}

function canonicalSelection(row: EditorRow): number[] {
  const selection = row.selection.map(Number);
  return betTypeConfig(row.betType).unordered
    ? [...selection].sort((left, right) => left - right)
    : selection;
}

function actionSummary(payload: FreeformPayload): string {
  const total = payload.bets.reduce((sum, bet) => sum + (bet.amountYen ?? 0), 0);
  return `点数 ${payload.bets.length}点・合計金額 ${total.toLocaleString("ja-JP")}円`;
}

export function FreeformPurchaseForm({
  raceId,
  horseNumbers,
  submit,
  disabled = false,
}: FreeformPurchaseFormProps) {
  const nextRowId = useRef(0);
  const [rows, setRows] = useState<EditorRow[]>([]);
  const [submission, setSubmission] = useState<SubmissionState>({ phase: "idle" });

  const isSubmitting = submission.phase === "submitting";
  const controlsDisabled = disabled || isSubmitting;
  const rowErrors = rows.map((row) => ({
    amount: amountError(row.amountText),
    selection: selectionError(row.selection),
  }));
  const formError =
    rows.length === 0
      ? "買い目を1点以上追加してください。"
      : rowErrors.some((error) => error.amount !== null || error.selection !== null)
        ? "入力内容を確認してください。"
        : null;

  function resetSubmissionAfterEdit() {
    setSubmission((current) =>
      current.phase === "success" || current.phase === "failure" ? { phase: "idle" } : current,
    );
  }

  function addRow() {
    resetSubmissionAfterEdit();
    setRows((current) => [
      ...current,
      {
        id: nextRowId.current++,
        betType: "win",
        selection: selectionForType("win", horseNumbers),
        amountText: "",
      },
    ]);
  }

  function removeRow(id: number) {
    resetSubmissionAfterEdit();
    setRows((current) => current.filter((row) => row.id !== id));
  }

  function updateRow(id: number, update: (row: EditorRow) => EditorRow) {
    resetSubmissionAfterEdit();
    setRows((current) => current.map((row) => (row.id === id ? update(row) : row)));
  }

  function updateBetType(id: number, betType: BetType) {
    updateRow(id, (row) => ({
      ...row,
      betType,
      selection: selectionForType(betType, horseNumbers, row.selection),
    }));
  }

  function updateSelection(id: number, selectionIndex: number, horseNumber: number) {
    updateRow(id, (row) => ({
      ...row,
      selection: row.selection.map((current, index) =>
        index === selectionIndex ? horseNumber : current,
      ),
    }));
  }

  function createPayload(
    kind: FreeformPayload["kind"],
    clientRequestId: string,
  ): FreeformPayload {
    return {
      race_id: raceId,
      kind,
      bets: rows.map((row) => ({
        betType: row.betType,
        selection: canonicalSelection(row),
        amountYen: Number(row.amountText),
        oddsUsed: null,
      })),
      presented_snapshot: null,
      client_request_id: clientRequestId,
    };
  }

  async function send(payload: FreeformPayload) {
    setSubmission({ phase: "submitting", payload });
    try {
      await submit(payload);
      setRows([]);
      setSubmission({ phase: "success", payload });
    } catch (error) {
      setSubmission({ phase: "failure", payload, reason: submissionReason(error) });
    }
  }

  function record(kind: FreeformPayload["kind"]) {
    if (formError !== null || controlsDisabled) return;
    void send(createPayload(kind, crypto.randomUUID()));
  }

  function retry() {
    if (submission.phase !== "failure" || controlsDisabled) return;
    void send(submission.payload);
  }

  return (
    <section className="purchase-actions" aria-label="提示にない購入内容の記録">
      <p className="note">
        実際に購入した内容を入力してください。提示内容がないため、この記録では政策の比較線は動きません。
      </p>

      <form
        className="purchase-actions__editor"
        aria-label="自由入力フォーム"
        onSubmit={(event) => event.preventDefault()}
      >
        {rows.map((row, rowIndex) => {
          const config = betTypeConfig(row.betType);
          const errors = rowErrors[rowIndex];
          const displayIndex = rowIndex + 1;

          return (
            <fieldset
              className="purchase-actions__editor-row"
              data-testid="freeform-bet-row"
              key={row.id}
            >
              <legend>買い目 {displayIndex}</legend>

              <label htmlFor={`freeform-bet-type-${row.id}`}>券種</label>
              <select
                id={`freeform-bet-type-${row.id}`}
                aria-label={`${displayIndex}行目 券種`}
                value={row.betType}
                disabled={controlsDisabled}
                onChange={(event) => updateBetType(row.id, event.target.value as BetType)}
              >
                {BET_TYPES.map((betType) => (
                  <option value={betType.value} key={betType.value}>
                    {betType.label}
                  </option>
                ))}
              </select>

              {config.selectionLabels.map((label, selectionIndex) => (
                <span key={`${row.id}-${label}`}>
                  <label htmlFor={`freeform-selection-${row.id}-${selectionIndex}`}>{label}</label>
                  <select
                    id={`freeform-selection-${row.id}-${selectionIndex}`}
                    aria-label={`${displayIndex}行目 ${label}`}
                    value={row.selection[selectionIndex]}
                    disabled={controlsDisabled}
                    onChange={(event) =>
                      updateSelection(row.id, selectionIndex, Number(event.target.value))
                    }
                  >
                    {horseNumbers.length === 0 ? <option value="">馬番なし</option> : null}
                    {horseNumbers.map((horseNumber) => (
                      <option value={horseNumber} key={horseNumber}>
                        {horseNumber}
                      </option>
                    ))}
                  </select>
                </span>
              ))}

              <label htmlFor={`freeform-amount-${row.id}`}>金額</label>
              <input
                id={`freeform-amount-${row.id}`}
                aria-label={`${displayIndex}行目 金額`}
                type="number"
                inputMode="numeric"
                min={100}
                step={100}
                value={row.amountText}
                disabled={controlsDisabled}
                onChange={(event) =>
                  updateRow(row.id, (current) => ({
                    ...current,
                    amountText: event.target.value,
                  }))
                }
              />
              <span>円</span>

              <button
                type="button"
                onClick={() => removeRow(row.id)}
                disabled={controlsDisabled}
              >
                {displayIndex}行目を削除
              </button>

              {errors.selection ? (
                <p className="note" role="alert">
                  {errors.selection}
                </p>
              ) : null}
              {errors.amount ? (
                <p className="note" role="alert">
                  {errors.amount}
                </p>
              ) : null}
            </fieldset>
          );
        })}

        <button type="button" onClick={addRow} disabled={controlsDisabled}>
          買い目を追加
        </button>

        {rows.length === 0 ? (
          // guidance, not an alert: the empty editor is the normal initial state
          <p className="note">{formError}</p>
        ) : null}

        <div className="purchase-actions__buttons">
          <button
            className="purchase-actions__button"
            type="button"
            onClick={() => record("freeform")}
            disabled={controlsDisabled || formError !== null}
          >
            自由入力として記録
          </button>
          <button
            className="purchase-actions__button"
            type="button"
            onClick={() => record("presentation_unavailable")}
            disabled={controlsDisabled || formError !== null}
          >
            提示が確認できなかったが購入した
          </button>
        </div>
      </form>

      {submission.phase === "submitting" ? (
        <p className="note" role="status">
          送信中です
        </p>
      ) : null}
      {submission.phase === "success" ? (
        <p className="note" role="status" data-testid="freeform-record-success">
          記録しました: {actionSummary(submission.payload)}
        </p>
      ) : null}
      {submission.phase === "failure" ? (
        <div className="purchase-actions__failure" role="alert">
          <p className="note">記録できませんでした: {submission.reason}</p>
          <button type="button" onClick={retry} disabled={controlsDisabled}>
            同じ内容で再試行
          </button>
        </div>
      ) : null}
    </section>
  );
}
