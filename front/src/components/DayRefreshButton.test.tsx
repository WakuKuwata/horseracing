import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

  it("on completion refetches the races list AND the day's 注目条件 list", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () => batch("succeeded", 2, 0)),
    );
    const { queryClient } = renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    await screen.findByText(/完了 2\/2 成功/);

    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["races"]));
    // Feature 138: the day list is keyed by the date (other days' lists are left alone)
    expect(keys).toContain(JSON.stringify(["attention-day", DATE]));
    // ...and the rules list (prospective tallies / checkpoint records are read-time over results)
    expect(keys).toContain(JSON.stringify(["attention-rules"]));
  });

  it("polls the per-date 期待回収率 recompute(s) and refetches the 注目条件 day list once they land", async () => {
    // 138: the expected_return follow-up — not the refresh — writes each race's first computation
    // (scan + picks) and the latest ens15 row the day list reads, and it finishes AFTER the batch.
    const ER = "44444444-4444-4444-4444-444444444444";
    let erPolls = 0;
    const child = (job_id: string, scope_value: string) => ({
      job_id, job_type: "refresh_race", status: "succeeded", scope: "race", scope_value,
      retry_count: 0, followup_job_id: ER,
    });
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () =>
        // both children share the same per-date recompute (QUEUED reuse) → polled once
        batch("succeeded", 2, 0, {
          children: [child("j1", "202406050911"), child("j2", "202406050912")],
        }),
      ),
      http.get(`${BASE}/jobs/${ER}`, () => {
        erPolls += 1;
        return HttpResponse.json({ job_id: ER, job_type: "expected_return",
          status: erPolls < 3 ? "running" : "succeeded", scope: "date", scope_value: DATE,
          retry_count: 0 });
      }),
    );
    const { queryClient } = renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    await screen.findByText(/完了 2\/2 成功/);
    const calls = (key: unknown[]) =>
      invalidate.mock.calls.filter(
        (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(key),
      ).length;
    // once at the batch's end, once more when the (single, shared) recompute finishes
    await waitFor(() => expect(calls(["attention-day", DATE])).toBe(2));
    expect(calls(["attention-rules"])).toBe(2);
    expect(erPolls).toBeGreaterThanOrEqual(3);
    // the races list is not re-invalidated by the recompute (it does not read the ens15 rows)
    expect(calls(["races"])).toBe(1);
  });

  it("without follow-ups the day list is refetched exactly once (no phantom second refetch)", async () => {
    server.use(
      http.post(`${BASE}/days/${DATE}/refresh`, () => accept()),
      http.get(`${BASE}/batches/${TRACE}`, () => batch("succeeded", 2, 0)),
    );
    const { queryClient } = renderWithProviders(<DayRefreshButton date={DATE} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    await screen.findByText(/完了 2\/2 成功/);
    await new Promise((r) => setTimeout(r, 50));
    const n = invalidate.mock.calls.filter(
      (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(["attention-day", DATE]),
    ).length;
    expect(n).toBe(1);
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
