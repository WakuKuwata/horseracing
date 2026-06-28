import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

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
