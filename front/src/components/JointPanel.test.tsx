import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { http, HttpResponse, jointResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { JointPanel } from "./JointPanel";

describe("JointPanel selected comparison model (154)", () => {
  it("uses the selected model alongside joint parameters and refetches on a model switch", async () => {
    const urls: URL[] = [];
    server.use(http.get("*/api/v1/races/:id/predictions", ({ request }) => {
      const url = new URL(request.url);
      urls.push(url);
      return HttpResponse.json({ ...jointResponse,
        joint: [{ selection: [1,2], prob: url.searchParams.get("model_version") === "other-model" ? .12 : .21 }],
      });
    }));
    function Driver() {
      const [model, setModel] = useState("selected-model");
      return <><button onClick={() => setModel("other-model")}>switch-model</button>
        <JointPanel raceId="200806010111" modelVersion={model} /></>;
    }
    renderWithProviders(<Driver />);
    expect(await screen.findByText("21.00%")).toBeInTheDocument();
    expect(urls[0].searchParams.get("model_version")).toBe("selected-model");
    expect(urls[0].searchParams.get("bet_type")).toBe("quinella");
    expect(urls[0].searchParams.get("top")).toBe("10");
    await userEvent.click(screen.getByRole("button", { name: "switch-model" }));
    expect(await screen.findByText("12.00%")).toBeInTheDocument();
    await waitFor(() => expect(urls.some((url) => url.searchParams.get("model_version") === "other-model")).toBe(true));
  });

  it("keeps the adopted-model request contract when no model is supplied", async () => {
    let received: URL | undefined;
    server.use(http.get("*/api/v1/races/:id/predictions", ({ request }) => {
      received = new URL(request.url);
      return HttpResponse.json(jointResponse);
    }));
    renderWithProviders(<JointPanel raceId="200806010111" />);
    await screen.findByText("21.00%");
    expect(received?.searchParams.has("model_version")).toBe(false);
  });
});
