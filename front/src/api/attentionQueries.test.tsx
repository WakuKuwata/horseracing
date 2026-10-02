import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { useAttention, useAttentionDay, useAttentionRules } from "./queries";
import {
  ATTENTION_RULE_IDS,
  attentionAvailableFixture,
  attentionDayItemFixture,
  attentionHorseFixture,
  attentionNoChipHorseFixture,
  attentionRulesFixture,
  happyHandlers,
  http,
  HttpResponse,
} from "../tests/fixtures";
import { server } from "../tests/server";

const RID = "202610030511";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("Feature 138 attention queries (default happy handlers)", () => {
  it("useAttention reads the race's typed empty state under key ['attention', raceId]", async () => {
    server.use(...happyHandlers);
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useAttention(RID), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({
      status: "unavailable", race_id: RID, reason: "not_computed",
    });
    expect(client.getQueryData(["attention", RID])).toEqual(result.current.data);
  });

  it("useAttentionRules returns the 5 rules in fixed rank order under ['attention-rules']", async () => {
    server.use(...happyHandlers);
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useAttentionRules(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const items = result.current.data!.items;
    expect(items.map((r) => r.id)).toEqual(ATTENTION_RULE_IDS);
    expect(items.map((r) => r.rank)).toEqual([1, 2, 3, 4, 5]);
    // pre-launch: every rule is 研究中 with zero counted picks and no decision record
    for (const r of items) {
      expect(r.prospective.stage).toBe("researching");
      expect(r.prospective.n_counted).toBe(0);
      expect(r.prospective.decisions).toEqual([]);
      expect(r.backtest.valuation_basis).toBe("closing_odds_approx");
    }
    expect(client.getQueryData(["attention-rules"])).toBeDefined();
  });

  it("useAttentionDay echoes the date with an empty list under ['attention-day', date]", async () => {
    server.use(...happyHandlers);
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useAttentionDay("2026-10-03"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ date: "2026-10-03", items: [] });
    expect(client.getQueryData(["attention-day", "2026-10-03"])).toBeDefined();
  });

  it("useAttentionDay waits for a date (the API 422s on an empty one)", async () => {
    let hits = 0;
    server.use(
      http.get("*/api/v1/attention/day", () => {
        hits += 1;
        return HttpResponse.json({ date: "", items: [] });
      }),
    );
    const { wrapper } = setup();
    const { result } = renderHook(() => useAttentionDay(""), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(hits).toBe(0);
  });

  it("useAttention does not retry a failing call (settles straight to the error state)", async () => {
    let hits = 0;
    server.use(
      http.get("*/api/v1/races/:id/attention", () => {
        hits += 1;
        return HttpResponse.json(
          { status: 422, code: "invalid_race_id", detail: "bad id" }, { status: 422 },
        );
      }),
    );
    // a client WITH retries enabled: the hook's own retry:false must win
    const client = new QueryClient({ defaultOptions: { queries: { retry: 3, gcTime: 0 } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useAttention("bad"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.code).toBe("invalid_race_id");
    expect(hits).toBe(1);
  });
});

describe("Feature 138 fixture builders follow the API shape", () => {
  it("stages cover exactly the applicable rules; pick_status covers every rule", () => {
    const h = attentionHorseFixture();
    expect(Object.keys(h.stages).sort()).toEqual([...h.applicable].sort());
    expect(Object.keys(h.pick_status)).toEqual(ATTENTION_RULE_IDS);
    const s5Only = attentionHorseFixture({ applicable: ["S5"], chip_rule: null, chip_stage: null,
      chip_now: null, levels: null });
    expect(Object.keys(s5Only.stages)).toEqual(["S5"]);
    expect(s5Only.pick_status).toEqual({ S1: "none", S2: "none", S3: "none", S4: "none",
      S5: "pick" });
    const none = attentionNoChipHorseFixture();
    expect(none.applicable).toEqual([]);
    expect(none.stages).toEqual({});
    expect(none.chip_rule).toBeNull();
  });

  it("the S1 chip horse satisfies the inclusion S1 ⊂ S3, S1 ⊂ S4", () => {
    const { horses } = attentionAvailableFixture();
    for (const h of horses) {
      if (h.applicable.includes("S1")) {
        expect(h.applicable).toEqual(expect.arrayContaining(["S3", "S4"]));
      }
    }
    expect(attentionDayItemFixture().chip_rule).toBe("S1");
  });

  it("per-rule overrides merge the prospective block one level deep", () => {
    const r = attentionRulesFixture({ S4: { prospective: { stage: "failed", checkpoint: 300 } } });
    const s4 = r.items.find((i) => i.id === "S4")!;
    expect(s4.prospective.stage).toBe("failed");
    expect(s4.prospective.checkpoint).toBe(300);
    expect(s4.prospective.policy_version).toBe("v1");
    expect(s4.backtest.all.roi).toBeCloseTo(1.14284, 6);
    expect(r.items.find((i) => i.id === "S1")!.prospective.stage).toBe("researching");
  });
});
