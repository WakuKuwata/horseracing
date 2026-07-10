import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { happyHandlers } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { ShadowLogPage } from "./ShadowLogPage";

describe("ShadowLogPage", () => {
  it("renders the prospective shadow-log panel with its honest labels", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<ShadowLogPage />);
    expect(await screen.findByTestId("shadow-log-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("shadow-log-labels")).toHaveTextContent("前向き");
  });
});
