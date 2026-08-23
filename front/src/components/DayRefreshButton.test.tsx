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

function batch(status: string, succeeded: number, failed: number, extra: Record<string, unknown> = {}) {
  return HttpResponse.json({
    trace_id: TRACE, status, scope_value: DATE, total: 2, succeeded, failed, running: 0,
    partial: 0, skipped: 0, discovered: 2, enqueued: 2, children: [], ...extra,
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

  it("counts PARTIAL children instead of leaving them unaccounted for", async () => {
    // a race day mid-afternoon: races that have not run yet end PARTIAL (no result table yet).
    // The old line read 「完了 1/2 成功」 with 0 失敗 — the other race was invisible.
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () =>
        batch("partial", 1, 0, { partial: 1 }),
      ),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(await screen.findByText(/1 一部/)).toBeInTheDocument();
  });

  it("says nothing was re-fetched when every race was reused, and offers a forced retry", async () => {
    // the day still HAS its races (total), the click just enqueued none of them (enqueued)
    const forced: boolean[] = [];
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, async ({ request }) => {
        forced.push(((await request.json()) as { force?: boolean }).force === true);
        return accept();
      }),
      http.get(`${BASE}/batches/${TRACE}`, () =>
        batch("succeeded", 12, 0, { total: 12, discovered: 12, enqueued: 0 }),
      ),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));

    // never a green 「完了 12/12 成功」 for a click that fetched nothing
    expect(await screen.findByText(/いずれも直近に取得済み/)).toBeInTheDocument();
    expect(screen.queryByText(/完了 12\/12 成功/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "強制的に再取得" }));
    expect(forced).toEqual([false, true]);
  });

  it("reports races the batch never touched because their job was reused", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () =>
        batch("succeeded", 36, 0, { total: 36, discovered: 36, enqueued: 2 }),
      ),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(await screen.findByText(/うち 34 レースは直近取得済み/)).toBeInTheDocument();
  });

  it("shows a parent-level failure as an error, not as a completed batch", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () =>
        batch("failed", 0, 0, { total: 0, discovered: null, enqueued: null }),
      ),
    );
    renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    const status = await screen.findByText(/更新失敗: 対象レースを取得できませんでした/);
    expect(status.className).toContain("refresh__status--error");
  });
});
