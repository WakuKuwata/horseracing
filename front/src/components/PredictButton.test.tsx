import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PredictButton } from "./PredictButton";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";

const BASE = "*/ops/v1";
const RID = "202406050911";
const JOB = "22222222-2222-2222-2222-222222222222";

function accept() {
  return HttpResponse.json(
    { job_id: JOB, status: "queued", reused: false, scope: "race", scope_value: RID,
      poll_url: `/ops/v1/jobs/${JOB}` },
    { status: 202 },
  );
}

function job(status: string) {
  return HttpResponse.json({ job_id: JOB, job_type: "predict", status, scope: "race",
    scope_value: RID, retry_count: 0 });
}

const FOLLOWUP = "33333333-3333-3333-3333-333333333333";

function jobWithFollowup(status: string) {
  return HttpResponse.json({ job_id: JOB, job_type: "predict", status, scope: "race",
    scope_value: RID, retry_count: 0, followup_job_id: FOLLOWUP });
}

function followupJob(status: string, reason?: string) {
  return HttpResponse.json({ job_id: FOLLOWUP, job_type: "recommend", status, scope: "race",
    scope_value: RID, retry_count: 0, ...(reason ? { reason } : {}) });
}

describe("PredictButton", () => {
  it("follows the auto-recommend job to 予測・買い目生成完了 and refetches recommendations", async () => {
    let followupCalls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => jobWithFollowup("succeeded")),
      http.get(`${BASE}/jobs/${FOLLOWUP}`, () => {
        followupCalls += 1;
        return followupCalls <= 2 ? followupJob("running") : followupJob("succeeded");
      }),
    );
    const { queryClient } = renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    // stage 2: predictions landed, buy-ups still generating — and the button is usable again
    expect(await screen.findByText("予測完了・買い目生成中…")).toBeInTheDocument();
    expect(await screen.findByText("予測・買い目生成完了")).toBeInTheDocument();
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["predictions", RID] }),
    );
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["recommendations", RID] }),
    );
  });

  it("shows the odds-missing follow-up outcome without failing the prediction", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => jobWithFollowup("succeeded")),
      http.get(`${BASE}/jobs/${FOLLOWUP}`, () =>
        followupJob("skipped", `no win odds for race ${RID} (recommendations need odds)`)),
    );
    renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    expect(await screen.findByText("予測完了(買い目: オッズ未取得)")).toBeInTheDocument();
  });

  it("enqueues, polls to success, reaches 予測完了, and invalidates predictions", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("succeeded")),
    );
    const { queryClient } = renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    expect(await screen.findByText("予測完了")).toBeInTheDocument();
    // success refetches the 014 predictions section (prefix key ["predictions", raceId]).
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["predictions", RID] }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "予測する" })).toBeEnabled(),
    );
  });

  it("disables the button while a job is in flight (no double-submit)", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("running")),
    );
    renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());
    expect(await screen.findByText("予測生成中…")).toBeInTheDocument();
  });

  it("shows 対象なし for a skipped terminal status", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("skipped")),
    );
    renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    expect(await screen.findByText("対象なし")).toBeInTheDocument();
  });

  it("shows a poll error instead of a silent 予測中…, then recovers to 予測完了", async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () => accept()),
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
    renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);

    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    expect(await screen.findByText(/状態確認エラー/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "予測中…" })).toBeDisabled();
    expect(await screen.findByText("予測完了")).toBeInTheDocument();
  });

  it("surfaces a typed error without crashing", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/predict`, () =>
        HttpResponse.json({ status: 404, code: "race_not_found", detail: "race not found" },
          { status: 404 }),
      ),
    );
    renderWithProviders(<PredictButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "予測する" }));
    expect(await screen.findByText(/予測失敗/)).toBeInTheDocument();
  });
});
