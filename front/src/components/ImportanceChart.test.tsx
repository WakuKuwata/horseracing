import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { happyHandlers } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { ImportanceChart } from "./ImportanceChart";

describe("ImportanceChart", () => {
  it("renders split-gain importance with Japanese labels and narrow naming", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<ImportanceChart modelVersion="lgbm-006" />);
    await screen.findByText("走破時計（相対・平均）");
    expect(screen.getByText("騎手成績（統計）")).toBeInTheDocument();
    // narrow naming (gain), not general "feature importance"
    expect(screen.getByRole("heading", { name: /分割利得（gain）重要度/ })).toBeInTheDocument();
  });

  it("shows a typed 未収録 state (not error) when importance is absent", async () => {
    server.use(
      http.get("*/api/v1/models/:mv/importance", () =>
        HttpResponse.json({ status: 404, code: "importance_unavailable", detail: "none" }, { status: 404 }),
      ),
    );
    renderWithProviders(<ImportanceChart modelVersion="m-old" />);
    await screen.findByText(/収録されていません/);
  });
});
