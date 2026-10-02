import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RefreshButton } from "./RefreshButton";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";

const BASE = "*/ops/v1";
const RID = "202406050911";
const JOB = "11111111-1111-1111-1111-111111111111";

function accept() {
  return HttpResponse.json(
    { job_id: JOB, status: "queued", reused: false, scope: "race", scope_value: RID,
      poll_url: `/ops/v1/jobs/${JOB}` },
    { status: 202 },
  );
}

function job(status: string) {
  return HttpResponse.json({ job_id: JOB, job_type: "refresh_race", status, scope: "race",
    scope_value: RID, retry_count: 0 });
}

describe("RefreshButton", () => {
  it("enqueues, polls to success, and reaches 更新完了", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("succeeded")),
    );
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);

    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    expect(await screen.findByText("更新完了")).toBeInTheDocument();
    // button is re-enabled after a terminal status
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "データ更新" })).toBeEnabled(),
    );
  });

  it("shows 対象なし for a skipped terminal status", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("skipped")),
    );
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    expect(await screen.findByText("対象なし")).toBeInTheDocument();
  });

  it("on success refetches race, odds, predictions AND 期待回収率 (the views a refresh feeds)", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("succeeded")),
    );
    const { queryClient } = renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    await screen.findByText("更新完了");

    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["race", RID]));
    expect(keys).toContain(JSON.stringify(["odds", RID]));
    expect(keys).toContain(JSON.stringify(["predictions", RID]));
    // Feature 137: the market-aware expected return is recomputed by ops after a refresh
    expect(keys).toContain(JSON.stringify(["market-ev", RID]));
    // Feature 138: the 注目条件 view reads the same recompute (current values, chip_now, voids)
    expect(keys).toContain(JSON.stringify(["attention", RID]));
    // ...and the rules list's prospective tallies / checkpoint records (read-time over results),
    // so the expanded breakdown never disagrees with a chip that moved to 「300 点不通過」
    expect(keys).toContain(JSON.stringify(["attention-rules"]));
  });

  it("polls the 期待回収率 recompute it enqueued and refetches market-ev once that lands", async () => {
    const ER = "22222222-2222-2222-2222-222222222222";
    let erPolls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () =>
        HttpResponse.json({ job_id: JOB, job_type: "refresh_race", status: "succeeded",
          scope: "race", scope_value: RID, retry_count: 0, followup_job_id: ER })),
      http.get(`${BASE}/jobs/${ER}`, () => {
        erPolls += 1;
        return HttpResponse.json({ job_id: ER, job_type: "expected_return",
          status: erPolls < 3 ? "running" : "succeeded", scope: "date",
          scope_value: "2024-06-05", retry_count: 0 });
      }),
    );
    const { queryClient } = renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    await screen.findByText("更新完了");
    const marketEvCalls = () =>
      invalidate.mock.calls.filter(
        (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(["market-ev", RID]),
      ).length;
    // once at the refresh's terminal status, once more when the recompute job finishes
    await waitFor(() => expect(marketEvCalls()).toBe(2));
    expect(erPolls).toBeGreaterThanOrEqual(3);
  });

  it("refetches the 注目条件 view in BOTH completion paths (refresh, then the recompute)", async () => {
    // Feature 138: the recompute job writes the race's first computation (scan + picks) and the
    // latest ens15 row — so the attention view must be refetched when it lands, not only when the
    // refresh itself finishes (otherwise the chips appear only after a manual reload).
    const ER = "33333333-3333-3333-3333-333333333333";
    let erPolls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () =>
        HttpResponse.json({ job_id: JOB, job_type: "refresh_race", status: "succeeded",
          scope: "race", scope_value: RID, retry_count: 0, followup_job_id: ER })),
      http.get(`${BASE}/jobs/${ER}`, () => {
        erPolls += 1;
        return HttpResponse.json({ job_id: ER, job_type: "expected_return",
          status: erPolls < 3 ? "running" : "succeeded", scope: "date",
          scope_value: "2024-06-05", retry_count: 0 });
      }),
    );
    const { queryClient } = renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    await screen.findByText("更新完了");
    const attentionCalls = () =>
      invalidate.mock.calls.filter(
        (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(["attention", RID]),
      ).length;
    // the refresh's terminal status has invalidated once; the recompute is still running
    await waitFor(() => expect(attentionCalls()).toBeGreaterThanOrEqual(1));
    await waitFor(() => expect(attentionCalls()).toBe(2));
    expect(erPolls).toBeGreaterThanOrEqual(3);
    // the rules list follows both paths too
    const rulesCalls = invalidate.mock.calls.filter(
      (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(["attention-rules"]),
    ).length;
    expect(rulesCalls).toBe(2);
  });

  it("shows a poll error instead of a silent 更新中…, then recovers to 更新完了", async () => {
    // Regression: the job status endpoint once 500'd exactly at the terminal transition — the
    // button sat on 更新中… forever with no hint. The poll error must be visible, and polling must
    // keep going so a recovered endpoint still settles the button.
    let calls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => {
        calls += 1;
        return calls <= 2
          ? HttpResponse.json(
              { status: 500, code: "internal", detail: "boom" },
              { status: 500 },
            )
          : job("succeeded");
      }),
    );
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);

    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    expect(await screen.findByText(/状態確認エラー/)).toBeInTheDocument();
    // still disabled — the job may well be running server-side
    expect(screen.getByRole("button", { name: "更新中…" })).toBeDisabled();
    // once the endpoint recovers, the terminal state lands
    expect(await screen.findByText("更新完了")).toBeInTheDocument();
  });

  it("surfaces a typed error without crashing", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/refresh`, () =>
        HttpResponse.json({ status: 404, code: "race_not_found", detail: "race not found" },
          { status: 404 }),
      ),
    );
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "データ更新" }));
    expect(await screen.findByText(/更新失敗/)).toBeInTheDocument();
  });
});
