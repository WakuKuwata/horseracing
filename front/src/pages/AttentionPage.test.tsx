import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ATTENTION_NOTE_TEXT } from "../components/AttentionNote";
import { routes } from "../router";
import { assertListDiscipline, assertRoiBasisLabels } from "../tests/attentionScope";
import { ATTENTION_RULE_IDS, happyHandlers } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { AttentionPage } from "./AttentionPage";

const FR011 =
  "注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。" +
  "S1・S2 は結果を見てから見つけた条件で、過去検証の p 値は多重探索を補正していません。" +
  "選定には現在のオッズを使うため、購入時点の価格が違えば結果も変わります。" +
  "注目条件はレースの最初の計算時点で判定し、その後のオッズ変化では付け外ししません。" +
  "的中や利益を保証するものではありません。";

describe("AttentionPage /attention (138 T036)", () => {
  it("shows the standing note (FR-011 verbatim) and the rules list already open", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<AttentionPage />);

    expect(screen.getByRole("heading", { name: "注目条件" })).toBeInTheDocument();
    expect(ATTENTION_NOTE_TEXT).toBe(FR011);
    expect(screen.getByTestId("attention-note")).toHaveTextContent(FR011);

    const panel = screen.getByTestId("attention-rules-panel") as HTMLDetailsElement;
    expect(panel.open).toBe(true);
    await screen.findByTestId("attention-rule-S1");
    const order = Array.from(panel.querySelectorAll("[data-rule-id]")).map((e) =>
      e.getAttribute("data-rule-id"),
    );
    expect(order).toEqual(ATTENTION_RULE_IDS);

    // the note precedes the list
    const note = screen.getByTestId("attention-note");
    expect(note.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // discipline over each attention component's own DOM (never the whole page)
    assertListDiscipline(note);
    assertListDiscipline(panel);
    expect(assertRoiBasisLabels(panel).length).toBeGreaterThan(0);
  });

  it("is routed at /attention and linked from the header nav as 「注目条件」", async () => {
    server.use(...happyHandlers);
    const router = createMemoryRouter(routes, {
      initialEntries: ["/attention"],
      future: {
        v7_relativeSplatPath: true,
        v7_fetcherPersist: true,
        v7_normalizeFormMethod: true,
        v7_partialHydration: true,
        v7_skipActionErrorRevalidation: true,
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId("attention-page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "注目条件" })).toHaveAttribute("href", "/attention");
    expect(await screen.findByTestId("attention-rule-S5")).toBeInTheDocument();
  });
});
