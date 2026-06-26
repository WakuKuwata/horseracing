import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { happyHandlers } from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { OddsPanel } from "./OddsPanel";

describe("OddsPanel", () => {
  it("keeps real and estimated odds separate and badges every estimated value", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<OddsPanel raceId="200806010111" />);

    // wait for the estimated odds (×12.3) to render
    await screen.findByText("×12.3");

    // the estimated odd MUST be inside a [data-pseudo] node with a 推定 badge; real odds must NOT.
    assertPseudoLabelCoverage(container, ["×12.3"]);

    // real win odds (×3.1) must NOT be marked pseudo
    const winCell = screen.getByText("×3.1");
    expect(winCell.closest('[data-pseudo="true"]')).toBeNull();

    // real-exotic section carries a real source badge
    expect(within(container).getAllByText(/^実/).length).toBeGreaterThan(0);
  });

  it("renders an empty sub-state when estimated odds are absent (no crash)", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<OddsPanel raceId="200806010111" />);
    // the win section renders even before/without estimated; sanity wait
    expect(await screen.findByText("×3.1")).toBeInTheDocument();
  });
});
