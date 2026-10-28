import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useRoutes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { routes } from "./router";
import { happyHandlers } from "./tests/fixtures";
import { server } from "./tests/server";

// The same route table through the component router: a data router's client-side navigation
// builds a fetch Request, and jsdom's AbortSignal is not the one undici expects (MSW rejects it).
function RouteHost() {
  return useRoutes(routes);
}

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[path]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <RouteHost />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("B12: the 065 shadow-log page is closed", () => {
  it("redirects /shadow-log to the 期待回収率 prospective record (B8) and drops the nav link", async () => {
    server.use(...happyHandlers);
    renderAt("/shadow-log");

    expect(await screen.findByTestId("market-ev-prospective-page")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/market-ev/prospective");

    // The header keeps the ROI record's link; the closed instrument has none.
    expect(screen.getByRole("link", { name: "期待回収率の前向き現況" })).toHaveAttribute(
      "href",
      "/market-ev/prospective",
    );
    expect(screen.queryByRole("link", { name: /shadow-log/ })).toBeNull();
    expect(screen.queryByText(/prospective shadow-log/)).toBeNull();
  });
});
