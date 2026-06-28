import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { happyHandlers } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { CalibrationChart } from "./CalibrationChart";

describe("CalibrationChart", () => {
  it("renders OOS reliability with counts + CI and audit (SC-003)", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<CalibrationChart modelVersion="lgbm-006" />);

    // realized rate of the first (well-populated) bin + its CI
    await screen.findByText("6.0%");
    expect(screen.getByText(/\[3\.0%–9\.0%\]/)).toBeInTheDocument();

    // audit: walk-forward OOS provenance + model_version are shown on screen (not buried)
    expect(screen.getByText(/walk_forward_oos/)).toBeInTheDocument();
    expect(screen.getByText("lgbm-006")).toBeInTheDocument();
  });

  it("flags low-count bins as 件数不足 instead of plotting them as fact (R5)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<CalibrationChart modelVersion="lgbm-006" />);
    await screen.findByText("6.0%");

    expect(screen.getByText("件数不足")).toBeInTheDocument();
    const suppressed = container.querySelector('tr[data-suppressed="true"]');
    expect(suppressed).not.toBeNull();
    // the suppressed bin does NOT present its realized rate (50.0%) as a fact
    expect(suppressed?.textContent).not.toContain("50.0%");
  });
});
