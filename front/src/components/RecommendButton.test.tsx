import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecommendButton } from "./RecommendButton";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";

const BASE = "*/ops/v1";
const RID = "202406050911";
const JOB = "33333333-3333-3333-3333-333333333333";

function accept() {
  return HttpResponse.json(
    { job_id: JOB, status: "queued", reused: false, scope: "race", scope_value: RID,
      poll_url: `/ops/v1/jobs/${JOB}` },
    { status: 202 },
  );
}

function job(status: string, reason?: string) {
  return HttpResponse.json({ job_id: JOB, job_type: "recommend", status, scope: "race",
    scope_value: RID, retry_count: 0, ...(reason ? { reason } : {}) });
}

describe("RecommendButton", () => {
  it("shows a poll error instead of a silent 生成中…, then recovers to 生成完了", async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () => accept()),
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
    renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);

    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText(/状態確認エラー/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成中…" })).toBeDisabled();
    expect(await screen.findByText("生成完了")).toBeInTheDocument();
  });

  it("enqueues, polls to success, and invalidates recommendations (not predictions)", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("succeeded")),
    );
    const { queryClient } = renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText("生成完了")).toBeInTheDocument();
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["recommendations", RID] }),
    );
  });

  it("shows 対象なし for a skipped terminal status without a reason", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("skipped")),
    );
    renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText("対象なし")).toBeInTheDocument();
  });

  // Skip reasons are distinguished so a benign "already generated" doesn't read as a failure and
  // the two real prerequisites tell the user which button to press first.
  it.each([
    ["recommendations already exist for run 42", /生成済み/],
    ["no prediction_run for race 202406050911 (predict first)", /予測未生成/],
    ["no win odds for race 202406050911 (recommendations need odds)", /オッズ未取得/],
  ])("maps skip reason %s to a specific label", async (reason, expected) => {
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("skipped", reason)),
    );
    renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("surfaces a typed error without crashing", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () =>
        HttpResponse.json({ status: 404, code: "race_not_found", detail: "race not found" },
          { status: 404 }),
      ),
    );
    renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText(/生成失敗/)).toBeInTheDocument();
  });
});
