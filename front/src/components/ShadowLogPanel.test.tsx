import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { http, HttpResponse, happyHandlers, shadowLogResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { ShadowLogPanel } from "./ShadowLogPanel";

const BASE = "*/api/v1";

describe("ShadowLogPanel", () => {
  it("shows honest prospective labels, no profit language / no P/L coloring", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<ShadowLogPanel />);
    const labels = await screen.findByTestId("shadow-log-labels");
    expect(labels).toHaveTextContent("実際に約定できたオッズ");
    expect(labels).toHaveTextContent("前向き");
    expect(labels).toHaveTextContent("将来の的中・利益を約束するものではありません");
    expect(container.textContent).not.toMatch(/儲か|勝てる|稼げる/);
    // recovery <1 shown as a neutral fact (no color hooks / data-result)
    const stats = await screen.findByTestId("shadow-log-stats");
    expect(stats).toHaveTextContent("×0.90");
    expect(stats.querySelectorAll("[data-result]").length).toBe(0);
  });

  it("shows pending / void / weak-pretime counts (denominator states visible)", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<ShadowLogPanel />);
    const stats = await screen.findByTestId("shadow-log-stats");
    expect(stats).toHaveTextContent("集計待ち(未確定)");
    expect(stats).toHaveTextContent("無効(void)");
    expect(stats).toHaveTextContent("発走前保証が弱い");
  });

  it("shows an honest empty state (not fake metrics) when the instrument is still filling", async () => {
    server.use(
      http.get(`${BASE}/shadow-log`, () =>
        HttpResponse.json({ ...shadowLogResponse, n_prospective: 0, n_settled: 0, by_month: [] }),
      ),
    );
    renderWithProviders(<ShadowLogPanel />);
    expect(await screen.findByText(/まだ前向きデータがありません/)).toBeInTheDocument();
    // no fabricated recovery number when empty
    expect(screen.queryByTestId("shadow-log-stats")).toBeNull();
  });
});
