import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PurchaseActions } from "./PurchaseActions";
import type { PresentedBet, PurchaseActionsProps } from "./PurchaseActions";

const presentedBets: PresentedBet[] = [
  { betType: "win", selection: [3], amountYen: 500, oddsUsed: 4.2 },
  { betType: "quinella", selection: [2, 7], amountYen: null, oddsUsed: 12.5 },
];

function props(
  submit: PurchaseActionsProps["submit"],
  overrides: Partial<PurchaseActionsProps> = {},
): PurchaseActionsProps {
  return {
    raceId: "race-2026-09-02-01",
    presentedBets,
    predictionRunId: "run-106",
    winPolicy: "selected",
    presentationAvailable: true,
    hasResults: false,
    submit,
    existingRecord: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PurchaseActions", () => {
  it("そのまま購入の内容と描画時スナップショットを送る", async () => {
    const submit = vi.fn().mockResolvedValue({ record_id: "record-1" });
    render(<PurchaseActions {...props(submit)} />);

    fireEvent.click(screen.getByRole("button", { name: "そのまま購入を記録" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toEqual({
      race_id: "race-2026-09-02-01",
      kind: "as_presented",
      bets: [{ betType: "win", selection: [3], amountYen: 500, oddsUsed: 4.2 }],
      presented_snapshot: {
        bets: [
          { betType: "win", selection: [3], amountYen: 500, oddsUsed: 4.2 },
          { betType: "quinella", selection: [2, 7], amountYen: null, oddsUsed: 12.5 },
        ],
        win_policy: "selected",
        prediction_run_id: "run-106",
        snapshot_schema_version: 1,
      },
      client_request_id: expect.any(String),
    });
  });

  it.each([
    {
      name: "提示0点",
      overrides: { presentedBets: [], presentationAvailable: true },
      kind: "no_recommendation",
      snapshot: {
        bets: [],
        win_policy: "selected",
        prediction_run_id: "run-106",
        snapshot_schema_version: 1,
      },
    },
    {
      name: "提示の取得失敗",
      overrides: { presentedBets: [], presentationAvailable: false },
      kind: "presentation_unavailable",
      snapshot: null,
    },
    {
      name: "提示あり",
      overrides: { presentedBets, presentationAvailable: true },
      kind: "skipped_presented",
      snapshot: {
        bets: presentedBets,
        win_policy: "selected",
        prediction_run_id: "run-106",
        snapshot_schema_version: 1,
      },
    },
  ])("$name の見送り種別を送る", async ({ overrides, kind, snapshot }) => {
    const submit = vi.fn().mockResolvedValue({ record_id: "skip-record" });
    render(<PurchaseActions {...props(submit, overrides)} />);

    fireEvent.click(screen.getByRole("button", { name: "見送りを記録" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toEqual({
      race_id: "race-2026-09-02-01",
      kind,
      bets: [],
      presented_snapshot: snapshot,
      client_request_id: expect.any(String),
    });
  });

  it("変更した金額と行除外を反映し、100円単位でない金額は送らない", async () => {
    const editableBets: PresentedBet[] = [
      { betType: "win", selection: [3], amountYen: 1_000, oddsUsed: 4.2 },
      { betType: "quinella", selection: [2, 7], amountYen: 2_000, oddsUsed: 12.5 },
    ];
    const submit = vi.fn().mockResolvedValue({ record_id: "modified-record" });
    render(<PurchaseActions {...props(submit, { presentedBets: editableBets })} />);

    fireEvent.click(screen.getByRole("button", { name: "変更して購入を記録" }));
    const amount = screen.getByTestId("modified-amount-0");
    fireEvent.change(amount, { target: { value: "150" } });

    expect(screen.getByTestId("modified-error").textContent).toContain("100円単位");
    const recordButton = screen.getByRole("button", { name: "変更内容を記録" });
    expect(recordButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(recordButton);
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(amount, { target: { value: "1200" } });
    fireEvent.click(screen.getByTestId("modified-exclude-1"));
    fireEvent.click(recordButton);

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toEqual({
      race_id: "race-2026-09-02-01",
      kind: "modified",
      bets: [{ betType: "win", selection: [3], amountYen: 1_200, oddsUsed: 4.2 }],
      presented_snapshot: {
        bets: editableBets,
        win_policy: "selected",
        prediction_run_id: "run-106",
        snapshot_schema_version: 1,
      },
      client_request_id: expect.any(String),
    });
  });

  it("既存記録がある場合は訂正として送る", async () => {
    const submit = vi.fn().mockResolvedValue({ record_id: "correction-record" });
    render(
      <PurchaseActions
        {...props(submit, {
          existingRecord: { kind: "as_presented", recordId: "record-original" },
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "そのまま購入を訂正として記録" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toEqual({
      race_id: "race-2026-09-02-01",
      kind: "correction",
      bets: [{ betType: "win", selection: [3], amountYen: 500, oddsUsed: 4.2 }],
      presented_snapshot: null,
      client_request_id: expect.any(String),
      corrects_record_id: "record-original",
    });
  });

  it("失敗後の再試行では同じ client_request_id を使う", async () => {
    const submit = vi
      .fn()
      .mockRejectedValueOnce(new Error("接続できませんでした"))
      .mockResolvedValueOnce({ record_id: "record-after-retry" });
    render(<PurchaseActions {...props(submit)} />);

    fireEvent.click(screen.getByRole("button", { name: "そのまま購入を記録" }));
    await screen.findByText(/接続できませんでした/);
    const firstPayload = submit.mock.calls[0][0];

    fireEvent.click(screen.getByRole("button", { name: "同じ内容で再試行" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    const retriedPayload = submit.mock.calls[1][0];

    expect(retriedPayload.client_request_id).toBe(firstPayload.client_request_id);
    expect(retriedPayload).toEqual(firstPayload);
  });

  it("中立な記録文言だけを描画する", () => {
    const submit = vi.fn().mockResolvedValue({ record_id: "unused" });
    const { container } = render(<PurchaseActions {...props(submit, { hasResults: true })} />);

    expect(container.textContent).toContain("結果取込後の記録(事後入力)として保存されます");
    expect(container.textContent).not.toMatch(/利益|儲|おすすめ|買うべき/);
  });
});
