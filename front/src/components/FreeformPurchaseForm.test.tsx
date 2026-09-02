import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FreeformPurchaseForm } from "./FreeformPurchaseForm";
import type { FreeformPayload, FreeformPurchaseFormProps } from "./FreeformPurchaseForm";

const HORSE_NUMBERS = [2, 3, 7, 8, 10];
const REQUEST_ID = "10600000-0000-4000-8000-000000000023";

function props(
  submit: FreeformPurchaseFormProps["submit"],
  overrides: Partial<FreeformPurchaseFormProps> = {},
): FreeformPurchaseFormProps {
  return {
    raceId: "202609020101",
    horseNumbers: HORSE_NUMBERS,
    submit,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FreeformPurchaseForm", () => {
  it("馬単の順序を保ち、三連複を昇順に正規化して自由入力として記録する", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue({ record_id: "freeform-1" });
    render(<FreeformPurchaseForm {...props(submit)} />);

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.selectOptions(screen.getByLabelText("1行目 券種"), "exacta");
    await user.selectOptions(screen.getByLabelText("1行目 1着"), "8");
    await user.selectOptions(screen.getByLabelText("1行目 2着"), "2");
    await user.type(screen.getByLabelText("1行目 金額"), "1200");

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.selectOptions(screen.getByLabelText("2行目 券種"), "trio");
    await user.selectOptions(screen.getByLabelText("2行目 1頭目"), "10");
    await user.selectOptions(screen.getByLabelText("2行目 2頭目"), "3");
    await user.selectOptions(screen.getByLabelText("2行目 3頭目"), "7");
    await user.type(screen.getByLabelText("2行目 金額"), "600");

    await user.click(screen.getByRole("button", { name: "自由入力として記録" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    const expectedPayload: FreeformPayload = {
      race_id: "202609020101",
      kind: "freeform",
      bets: [
        { betType: "exacta", selection: [8, 2], amountYen: 1200, oddsUsed: null },
        { betType: "trio", selection: [3, 7, 10], amountYen: 600, oddsUsed: null },
      ],
      presented_snapshot: null,
      client_request_id: REQUEST_ID,
    };
    expect(submit.mock.calls[0][0]).toEqual(expectedPayload);
  });

  it("100円単位でない金額をインライン表示し、送信しない", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue({ record_id: "unused" });
    render(<FreeformPurchaseForm {...props(submit)} />);

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.type(screen.getByLabelText("1行目 金額"), "150");

    expect(screen.getByRole("alert", { name: "" })).toHaveTextContent("100円単位");
    expect(screen.getByRole("button", { name: "自由入力として記録" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "自由入力として記録" }));
    expect(submit).not.toHaveBeenCalled();
  });

  it("1行内の同じ馬番をインライン表示し、送信しない", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue({ record_id: "unused" });
    render(<FreeformPurchaseForm {...props(submit)} />);

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.selectOptions(screen.getByLabelText("1行目 券種"), "quinella");
    await user.selectOptions(screen.getByLabelText("1行目 1頭目"), "7");
    await user.selectOptions(screen.getByLabelText("1行目 2頭目"), "7");
    await user.type(screen.getByLabelText("1行目 金額"), "500");

    expect(screen.getByText("同じ馬番を重複して選択することはできません。")).toBeVisible();
    expect(screen.getByRole("button", { name: "自由入力として記録" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "自由入力として記録" }));
    expect(submit).not.toHaveBeenCalled();
  });

  it("失敗後の再試行で同じ client_request_id と内容を送る", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    const user = userEvent.setup();
    const submit = vi
      .fn()
      .mockRejectedValueOnce(new Error("接続できませんでした"))
      .mockResolvedValueOnce({ record_id: "freeform-after-retry" });
    render(<FreeformPurchaseForm {...props(submit)} />);

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.type(screen.getByLabelText("1行目 金額"), "700");
    await user.click(screen.getByRole("button", { name: "提示が確認できなかったが購入した" }));

    await screen.findByText(/接続できませんでした/);
    const firstPayload = submit.mock.calls[0][0] as FreeformPayload;
    expect(firstPayload.client_request_id).toBe(REQUEST_ID);

    await user.click(screen.getByRole("button", { name: "同じ内容で再試行" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    const retriedPayload = submit.mock.calls[1][0] as FreeformPayload;

    expect(retriedPayload.client_request_id).toBe(firstPayload.client_request_id);
    expect(retriedPayload).toEqual(firstPayload);
  });

  it("成功後は買い目を空に戻し、点数と合計金額を表示する", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue({ record_id: "freeform-success" });
    render(<FreeformPurchaseForm {...props(submit)} />);

    await user.click(screen.getByRole("button", { name: "買い目を追加" }));
    await user.type(screen.getByLabelText("1行目 金額"), "900");
    await user.click(screen.getByRole("button", { name: "自由入力として記録" }));

    const summary = await screen.findByTestId("freeform-record-success");
    expect(summary).toHaveTextContent("点数 1点");
    expect(summary).toHaveTextContent("合計金額 900円");
    expect(screen.queryAllByTestId("freeform-bet-row")).toHaveLength(0);
    expect(within(screen.getByRole("form")).getByRole("button", { name: "買い目を追加" })).toBeVisible();
  });
});
