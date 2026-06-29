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

describe("PredictButton", () => {
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
