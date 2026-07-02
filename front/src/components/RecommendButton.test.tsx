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

function job(status: string) {
  return HttpResponse.json({ job_id: JOB, job_type: "recommend", status, scope: "race",
    scope_value: RID, retry_count: 0 });
}

describe("RecommendButton", () => {
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

  it("shows 対象なし for a skipped terminal status (no odds / no prediction)", async () => {
    server.use(
      http.post(`${BASE}/races/${RID}/recommend`, () => accept()),
      http.get(`${BASE}/jobs/${JOB}`, () => job("skipped")),
    );
    renderWithProviders(<RecommendButton raceId={RID} pollMs={10} />);
    await userEvent.click(screen.getByRole("button", { name: "買い目生成" }));
    expect(await screen.findByText("対象なし")).toBeInTheDocument();
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
