import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { JobsPage } from "./JobsPage";

const BASE = "*/api/v1";

const jobs = {
  items: [
    { ingestion_job_id: "j1", source: "netkeiba", job_type: "predict", scope: "race",
      scope_value: "202501010106", status: "succeeded", trace_id: null, retry_count: 0,
      started_at: "2025-01-05T01:00:00Z", completed_at: "2025-01-05T01:01:00Z",
      error_message: null, processed_rows: 16, skipped_rows: 0, error_count: 0,
      created_at: "2025-01-05T01:00:00Z" },
    { ingestion_job_id: "j2", source: "netkeiba", job_type: "refresh_race", scope: "race",
      scope_value: "202501010107", status: "failed", trace_id: null, retry_count: 2,
      started_at: "2025-01-05T02:00:00Z", completed_at: null,
      error_message: "fetch blocked (403)", processed_rows: null, skipped_rows: null,
      error_count: null, created_at: "2025-01-05T02:00:00Z" },
  ],
};

describe("JobsPage", () => {
  it("lists jobs newest-first with status badges and inline error message", async () => {
    server.use(http.get(`${BASE}/jobs`, () => HttpResponse.json(jobs)));
    const { container } = renderWithProviders(<JobsPage />);
    await screen.findByText("202501010106");
    expect(screen.getByText("fetch blocked (403)")).toBeInTheDocument();
    expect(container.querySelector('tr[data-status="failed"]')).not.toBeNull();
    expect(container.textContent).not.toContain("NaN");
  });

  it("passes the status filter through to the API", async () => {
    let lastStatus: string | null = null;
    server.use(http.get(`${BASE}/jobs`, ({ request }) => {
      lastStatus = new URL(request.url).searchParams.get("status");
      return HttpResponse.json({ items: [] });
    }));
    renderWithProviders(<JobsPage />);
    await screen.findByText("該当するジョブがありません");
    await userEvent.selectOptions(screen.getByLabelText("状態"), "failed");
    await screen.findByText("該当するジョブがありません");
    expect(lastStatus).toBe("failed");
  });
});
