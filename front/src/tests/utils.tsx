import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

// Fresh QueryClient per test (no retry, no cache bleed between tests).
export function renderWithProviders(
  ui: ReactElement,
  { route = "/" }: { route?: string } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const wrap = (node: ReactElement) => (
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[route]}
        future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
      >
        {node}
      </MemoryRouter>
    </QueryClientProvider>
  );
  const result = render(wrap(ui));
  // RTL's bare `rerender` drops the providers; re-wrap so a re-render keeps the same client/router.
  return { ...result, rerender: (next: ReactElement) => result.rerender(wrap(next)), queryClient: client };
}
