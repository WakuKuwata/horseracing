import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CAPTURE_ATTENTION_REASONS,
  CAPTURE_ELIGIBILITY_REASONS,
  capturePresentation,
} from "../lib/captureLabels";
import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { JobsPage } from "./JobsPage";

const BASE = "*/api/v1";

const baseJob = {
  ingestion_job_id: "j1", source: "netkeiba", job_type: "predict", scope: "race",
  scope_value: "202501010106", status: "succeeded", trace_id: null, retry_count: 0,
  started_at: "2025-01-05T01:00:00Z", completed_at: "2025-01-05T01:01:00Z",
  error_message: null, processed_rows: 16, skipped_rows: 0, error_count: 0,
  created_at: "2025-01-05T01:00:00Z", summary: null, capture: null,
};

const jobs = {
  items: [
    baseJob,
    { ingestion_job_id: "j2", source: "netkeiba", job_type: "refresh_race", scope: "race",
      scope_value: "202501010107", status: "failed", trace_id: null, retry_count: 2,
      started_at: "2025-01-05T02:00:00Z", completed_at: null,
      error_message: "fetch blocked (403)", processed_rows: null, skipped_rows: null,
      error_count: null, created_at: "2025-01-05T02:00:00Z", summary: null, capture: null },
  ],
};

function jobWithCapture(
  id: string,
  capture: {
    state: "started" | "launched" | "done";
    outcome: "captured" | "skipped" | "rejected" | "failed" | "unknown";
    reason?: string | null;
  },
  startedAt = "2025-01-05T01:00:00Z",
) {
  return {
    ...baseJob,
    ingestion_job_id: id,
    scope_value: id,
    started_at: startedAt,
    summary: { capture },
    capture: {
      ...capture,
      reason: capture.reason ?? null,
      capture_strength: null,
      confirmation_eligible: null,
      seconds_to_post: null,
      chaos_snapshot_id: null,
    },
  };
}

function reasonsFromEligibilityContract() {
  const contract = readFileSync(
    resolve(
      __dirname,
      "../../../specs/086-capture-on-predict/contracts/capture-eligibility.md",
    ),
    "utf8",
  );
  const section = contract.slice(
    contract.indexOf("## 4."),
    contract.indexOf("## 5."),
  );
  const groups = {
    "適格": new Set<string>(),
    "取得不可": new Set<string>(),
  };
  for (const line of section.split("\n")) {
    if (!line.startsWith("|")) continue;
    const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
    const classification = cells
      .map((cell) => cell.replaceAll("*", ""))
      .find(
        (cell) => cell.startsWith("適格") || cell.startsWith("取得不可"),
      )
      ?.startsWith("取得不可")
      ? "取得不可"
      : cells.some((cell) => cell.replaceAll("*", "").startsWith("適格"))
        ? "適格"
        : null;
    if (!classification) continue;
    for (const match of cells[0].matchAll(/`([^`]+)`/g)) {
      groups[classification].add(match[1]);
    }
  }
  return groups;
}

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

  it("keeps eligibility skips grey and renders all acquisition failures as attention", async () => {
    const items = [
      jobWithCapture("captured", {
        state: "done",
        outcome: "captured",
        reason: "ok",
      }),
      jobWithCapture("elapsed", {
        state: "done",
        outcome: "skipped",
        reason: "post_time_elapsed",
      }),
      jobWithCapture("settled", {
        state: "done",
        outcome: "skipped",
        reason: "result_settled",
      }),
      jobWithCapture("already", {
        state: "done",
        outcome: "skipped",
        reason: "already_captured",
      }),
      jobWithCapture("disabled", {
        state: "done",
        outcome: "skipped",
        reason: "auto_capture_disabled",
      }),
      ...CAPTURE_ATTENTION_REASONS.map((reason, index) =>
        jobWithCapture(`attention-${index}`, {
          state: "done",
          outcome:
            reason === "outer_timeout"
              ? "unknown"
              : reason === "fetch_failed"
                ? "failed"
                : "skipped",
          reason,
        }),
      ),
    ];
    server.use(http.get(`${BASE}/jobs`, () => HttpResponse.json({ items })));

    renderWithProviders(<JobsPage />);

    await screen.findByText("captured");
    expect(screen.getByTestId("capture-captured")).toHaveAttribute(
      "data-capture-tone",
      "neutral",
    );
    for (const id of ["elapsed", "settled", "already", "disabled"]) {
      expect(screen.getByTestId(`capture-${id}`)).toHaveAttribute(
        "data-capture-tone",
        "muted",
      );
    }
    for (let index = 0; index < CAPTURE_ATTENTION_REASONS.length; index += 1) {
      expect(screen.getByTestId(`capture-attention-${index}`)).toHaveAttribute(
        "data-capture-tone",
        "attention",
      );
    }
    expect(screen.getByTestId("capture-attention-2")).toHaveTextContent(
      "取得元へのアクセス失敗",
    );
    expect(
      screen.getByTestId("capture-attention-2").closest("tr"),
    ).toHaveAttribute("data-status", "succeeded");
  });

  it("treats in-flight capture as neutral and a stale in-flight record as attention", () => {
    const capturing = {
      state: "launched",
      outcome: "unknown",
      reason: null,
      capture_strength: null,
      confirmation_eligible: null,
      seconds_to_post: null,
      chaos_snapshot_id: null,
    } as const;

    expect(
      capturePresentation(
        capturing,
        "2025-01-05T01:00:00Z",
        Date.parse("2025-01-05T01:00:10Z"),
      ),
    ).toMatchObject({ label: "捕捉中", tone: "neutral" });
    expect(
      capturePresentation(
        capturing,
        "2025-01-05T01:00:00Z",
        Date.parse("2025-01-05T01:00:19Z"),
      ),
    ).toMatchObject({ label: "捕捉不明", tone: "attention" });
    expect(
      capturePresentation(
        { ...capturing, state: "started" },
        "2025-01-05T01:00:00Z",
        Date.parse("2025-01-05T01:01:00Z"),
      ),
    ).toMatchObject({ label: "捕捉中", tone: "neutral" });
  });

  it.each(["rejected", "failed", "unknown"] as const)(
    "renders completed %s capture as attention",
    (outcome) => {
      expect(
        capturePresentation(
          {
            state: "done",
            outcome,
            reason: null,
            capture_strength: null,
            confirmation_eligible: null,
            seconds_to_post: null,
            chaos_snapshot_id: null,
          },
          "2025-01-05T01:00:00Z",
        ).tone,
      ).toBe("attention");
    },
  );

  it("shows an unknown reason verbatim and defaults it to attention", () => {
    const display = capturePresentation(
      {
        state: "done",
        outcome: "skipped",
        reason: "brand_new_failure_mode",
        capture_strength: null,
        confirmation_eligible: null,
        seconds_to_post: null,
        chaos_snapshot_id: null,
      },
      "2025-01-05T01:00:00Z",
    );

    expect(display.tone).toBe("attention");
    expect(display.label).toContain("brand_new_failure_mode");
  });

  it("classifies exactly the contract's eligibility and acquisition-failure reasons", () => {
    const expected = reasonsFromEligibilityContract();

    expect(new Set(CAPTURE_ELIGIBILITY_REASONS)).toEqual(expected["適格"]);
    expect(new Set(CAPTURE_ATTENTION_REASONS)).toEqual(expected["取得不可"]);
    expect(
      new Set([...CAPTURE_ELIGIBILITY_REASONS, ...CAPTURE_ATTENTION_REASONS]),
    ).toEqual(new Set([...expected["適格"], ...expected["取得不可"]]));
  });
});
