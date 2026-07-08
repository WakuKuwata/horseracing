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

  it("on success refetches race, odds AND predictions (the views a refresh feeds)", async () => {
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
