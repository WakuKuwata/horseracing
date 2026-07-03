import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { RefreshRangeButton } from "./RefreshRangeButton";

const OPS = "*/ops/v1";

describe("RefreshRangeButton", () => {
  it("requires a confirm step, then POSTs the range and links to job history", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${OPS}/refresh-range`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(
          { job_id: "abcd1234-0000", status: "queued", reused: false, scope: "range",
            scope_value: "2025-01-05..2025-01-05", poll_url: "/ops/v1/jobs/abcd1234-0000" },
          { status: 202 });
      }),
    );
    renderWithProviders(<RefreshRangeButton dateFrom="2025-01-05" dateTo="2025-01-05" label="この日を更新" />);

    // no POST until confirm
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(body).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "実行" }));

    await screen.findByText("ジョブ履歴");
    expect(body).toEqual({ date_from: "2025-01-05", date_to: "2025-01-05" });
    expect(screen.getByText(/投入しました/)).toBeInTheDocument();
  });

  it("cancel aborts without POSTing", async () => {
    let posted = false;
    server.use(http.post(`${OPS}/refresh-range`, () => {
      posted = true;
      return HttpResponse.json({}, { status: 202 });
    }));
    renderWithProviders(<RefreshRangeButton dateFrom="2025-01-05" dateTo="2025-01-06" label="この範囲を更新" />);
    await userEvent.click(screen.getByRole("button", { name: "この範囲を更新" }));
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(posted).toBe(false);
    expect(screen.getByRole("button", { name: "この範囲を更新" })).toBeInTheDocument();
  });

  it("shows a typed error (422) inline", async () => {
    server.use(http.post(`${OPS}/refresh-range`, () =>
      HttpResponse.json(
        { status: 422, code: "range_too_wide", detail: "range must be <= 35 days" },
        { status: 422 })));
    renderWithProviders(<RefreshRangeButton dateFrom="2025-01-01" dateTo="2025-06-01" label="この範囲を更新" />);
    await userEvent.click(screen.getByRole("button", { name: "この範囲を更新" }));
    await userEvent.click(screen.getByRole("button", { name: "実行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("range_too_wide");
  });
});
