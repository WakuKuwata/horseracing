import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DayRefreshButton } from "./DayRefreshButton";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";

const BASE = "*/ops/v1";
const DATE = "2024-12-28";
const TRACE = "trace-123";

function accept() {
  return HttpResponse.json(
    {
      trace_id: TRACE,
      status: "running",
      scope: "day",
      scope_value: DATE,
      poll_url: `/ops/v1/batches/${TRACE}`,
      children: [
        { job_id: "j1", status: "queued", reused: false, scope: "race", scope_value: "202406050911", poll_url: "" },
        { job_id: "j2", status: "queued", reused: false, scope: "race", scope_value: "202406050912", poll_url: "" },
      ],
    },
    { status: 202 },
  );
}

function batch(status: string, succeeded: number, failed: number) {
  return HttpResponse.json({
    trace_id: TRACE, status, scope_value: DATE, total: 2, succeeded, failed, running: 0, children: [],
  });
}

describe("DayRefreshButton", () => {
  it("enqueues a day batch and shows per-day completion", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () => batch("succeeded", 2, 0)),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(await screen.findByText(/完了 2\/2 成功/)).toBeInTheDocument();
  });

  it("shows a batch poll error instead of silent progress, then recovers to 完了", async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () => {
        calls += 1;
        return calls <= 2
          ? HttpResponse.json(
              { status: 500, code: "internal", detail: "boom" },
              { status: 500 },
            )
          : batch("succeeded", 2, 0);
      }),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(await screen.findByText(/状態確認エラー/)).toBeInTheDocument();
    expect(await screen.findByText(/完了 2\/2 成功/)).toBeInTheDocument();
  });

  it("surfaces partial failure and offers a failed-only re-run", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () => batch("partial", 1, 1)),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(await screen.findByText(/1 失敗/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "失敗を再実行" })).toBeInTheDocument();
  });
});
