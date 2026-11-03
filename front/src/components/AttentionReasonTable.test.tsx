import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AttentionHorse } from "../api/types";
import { stageLabel } from "../lib/attention";
import { assertAttentionDiscipline } from "../tests/attentionDiscipline";
import {
  attentionHorseFixture,
  attentionReasonFixture,
  evSnapshotFixture,
  pickStatusFor,
  stageDetail,
  stagesFor,
} from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { AttentionReasonTable } from "./AttentionReasonTable";

const JUDGED_AT = "2008-06-01T00:12:00Z"; // 09:12 JST

function draw(horse: AttentionHorse, controlIds: AttentionHorse["applicable"] = ["S5"]) {
  return render(<AttentionReasonTable horse={horse} judgedAt={JUDGED_AT} controlIds={controlIds} />);
}

function statuses(row: HTMLElement): (string | null)[] {
  return Array.from(row.querySelectorAll("[data-status]")).map((e) => e.getAttribute("data-status"));
}

/** Every `N%` text node must sit inside a `[data-kind]` node (criterion / expected_return). */
function assertPercentagesAreKinded(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!/\d\s*%/.test(n.textContent ?? "")) continue;
    expect(n.parentElement?.closest("[data-kind]"), `"${n.textContent}"`).not.toBeNull();
  }
}

describe("AttentionReasonTable (177)", () => {
  it("scenario 1: S1 — three rows, judged and current all met, summary form 1", () => {
    const { container } = draw(attentionHorseFixture());
    expect(screen.getByTestId("attention-reason-summary").textContent).toBe(
      "判断時点(2008/06/01 09:12)では S1 の 3 条件をすべて満たしました。現在値でも満たしています。",
    );
    const table = screen.getByTestId("attention-reason-table");
    expect(Array.from(table.querySelectorAll('th[scope="col"]')).map((t) => t.textContent)).toEqual([
      "条件", "判断時点(2008/06/01 09:12)", "現在値(2008/06/01 15:00)",
    ]);
    const rows = screen.getAllByTestId("attention-reason-row");
    expect(rows).toHaveLength(3);
    expect(Array.from(table.querySelectorAll('th[scope="row"]')).map((t) => t.textContent)).toEqual([
      "期待回収率(15 seed 平均)が 120% 超",
      "単勝オッズが 20 倍以上 40 倍未満",
      "前走から 14〜112 日",
    ]);
    expect(rows[0]).toHaveTextContent("125.0%");
    expect(rows[0]).toHaveTextContent("124.6%");
    expect(rows[1]).toHaveTextContent("32.5 倍");
    expect(rows[1]).toHaveTextContent("30.8 倍");
    expect(rows[2]).toHaveTextContent("41 日");
    expect(rows[2]).toHaveTextContent("41 日(判断時点で固定)");
    for (const r of rows) expect(statuses(r)).toEqual(["met", "met"]);
    // the word is the status; the glyph is decoration
    for (const st of Array.from(table.querySelectorAll("[data-status]"))) {
      expect(st.textContent).toMatch(/満たす$/);
      expect(st.querySelector('[aria-hidden="true"]')?.textContent).toBe("○");
    }
    expect(screen.getByTestId("attention-reason-also").textContent).toBe(
      "S3・S4 にも該当(表示する条件は S1)。対照 S5 にも該当。",
    );
    expect(screen.getByTestId("attention-reason-sources")).toHaveTextContent(
      "判断時点の値はこのレースの最初の計算で凍結したもの、現在値は最新の計算の値です",
    );
    expect(screen.queryByTestId("attention-field-changed")).toBeNull();
    assertPseudoLabelCoverage(container, ["125.0%", "124.6%"]);
    assertPercentagesAreKinded(container);
    assertAttentionDiscipline(container);
  });

  it("scenario 2: current odds 45.0 → only the band row is 満たさない, summary names it", () => {
    const current = evSnapshotFixture({ odds: 45.0, ens_expected_return: 1.22 });
    draw(attentionHorseFixture({ chip_now: "no_longer", current }));
    const rows = screen.getAllByTestId("attention-reason-row");
    expect(statuses(rows[0])).toEqual(["met", "met"]);
    expect(statuses(rows[1])).toEqual(["met", "not_met"]);
    expect(statuses(rows[2])).toEqual(["met", "met"]);
    expect(rows[1].querySelectorAll("[data-status]")[1].textContent).toBe("× 満たさない");
    expect(screen.getByTestId("attention-reason-summary").textContent).toBe(
      "判断時点(2008/06/01 09:12)では S1 の 3 条件をすべて満たしました。現在値では単勝オッズ帯の条件を満たしていません。",
    );
  });

  it("scenario 3: no current computation → every current cell says 最新の計算なし", () => {
    draw(attentionHorseFixture({ chip_now: "unknown", current: null, field_changed_after_pick: true }));
    const rows = screen.getAllByTestId("attention-reason-row");
    for (const r of rows) {
      expect(statuses(r)).toEqual(["met", "no_current"]);
      expect(r.querySelectorAll("[data-status]")[1].textContent).toBe("— 最新の計算なし");
    }
    const cols = Array.from(screen.getByTestId("attention-reason-table").querySelectorAll('th[scope="col"]'));
    expect(cols[2].textContent).toBe("現在値");
    expect(screen.getByTestId("attention-reason-summary").textContent).toBe(
      "判断時点(2008/06/01 09:12)では S1 の 3 条件をすべて満たしました。現在値はありません(再計算待ち・オッズ欠落など)。",
    );
    expect(screen.getByTestId("attention-field-changed")).toHaveTextContent("出走馬がその後変わっています");
  });

  it("scenario 3b: a missing gap is 値なし (not 最新の計算なし, not 満たさない)", () => {
    const horse = attentionHorseFixture();
    draw(attentionHorseFixture({ reason: attentionReasonFixture({ judged: horse.judged, current: horse.current, gap: null }) }));
    const rows = screen.getAllByTestId("attention-reason-row");
    expect(statuses(rows[2])).toEqual(["missing", "missing"]);
    expect(rows[2].querySelectorAll("[data-status]")[0].textContent).toBe("— 値なし");
    expect(rows[2]).not.toHaveTextContent("最新の計算なし");
    expect(screen.getByTestId("attention-reason-summary").textContent).toBe(
      "判断時点(2008/06/01 09:12)では S1 の 3 条件のうち 2 つを満たしました(満たさない: 前走からの間隔は値なし)。" +
        "現在値では前走からの間隔が値なしです。",
    );
  });

  it("scenario 4: S3 — one row, no band, no gap, no also-line", () => {
    const applicable: AttentionHorse["applicable"] = ["S3"];
    draw(
      attentionHorseFixture({
        applicable, chip_rule: "S3", stages: stagesFor(applicable), pick_status: pickStatusFor(applicable),
      }),
      [],
    );
    expect(screen.getAllByTestId("attention-reason-row")).toHaveLength(1);
    expect(screen.getByTestId("attention-reason-table")).toHaveTextContent("期待回収率(15 seed 平均)が 120% 超");
    expect(screen.getByTestId("attention-reason-table")).not.toHaveTextContent("倍");
    expect(screen.getByTestId("attention-reason-summary").textContent).toBe(
      "判断時点(2008/06/01 09:12)では S3 の条件を満たしました。現在値でも満たしています。",
    );
    expect(screen.queryByTestId("attention-reason-also")).toBeNull();
  });

  it("scenario 5: S1 chip with S2 → the S2 threshold is spelled out, S3/S4 by id only", () => {
    const applicable: AttentionHorse["applicable"] = ["S1", "S2", "S3", "S4", "S5"];
    draw(attentionHorseFixture({ applicable, chip_s2: true, stages: stagesFor(applicable), pick_status: pickStatusFor(applicable) }));
    expect(screen.getByTestId("attention-reason-also").textContent).toBe(
      "S2(期待回収率 130% 超)にも該当。S3・S4 にも該当(表示する条件は S1)。対照 S5 にも該当。",
    );
  });

  it("scenario 6: S1 failed at 300, chip on S3 → S1 tagged, S4 plain, 「表示する条件は S3」", () => {
    const applicable: AttentionHorse["applicable"] = ["S1", "S3", "S4"];
    const failed = stageDetail("failed", 300);
    draw(
      attentionHorseFixture({
        applicable, chip_rule: "S3", chip_stage: stageDetail("observing"),
        stages: stagesFor(applicable, { S1: failed, S3: stageDetail("observing") }),
        pick_status: pickStatusFor(applicable),
      }),
      [],
    );
    expect(screen.getAllByTestId("attention-reason-row")).toHaveLength(1);
    expect(screen.getByTestId("attention-reason-also").textContent).toBe(
      `S1(${stageLabel(failed)})・S4 にも該当(表示する条件は S3)。`,
    );
  });

  it("scenario 7: an unavailable reason shows the fact only (no table, no summary)", () => {
    draw(attentionHorseFixture({ reason: { status: "unavailable", reason: "inconsistent" } }));
    expect(screen.queryByTestId("attention-reason-table")).toBeNull();
    expect(screen.queryByTestId("attention-reason-summary")).toBeNull();
    expect(screen.getByTestId("attention-reason-unavailable").textContent).toBe(
      "該当理由を表示できません(応答の整合が取れていません)",
    );
  });

  it("precision: an EV that rounds onto the threshold gets more digits, the status stays the API's", () => {
    const horse = attentionHorseFixture();
    const judged = evSnapshotFixture({ ens_expected_return: 1.2004, single_expected_return: 1.28 });
    draw(attentionHorseFixture({ judged, reason: attentionReasonFixture({ judged, current: horse.current }) }));
    const row = screen.getAllByTestId("attention-reason-row")[0];
    expect(row).toHaveTextContent("120.04%");
    expect(statuses(row)[0]).toBe("met");
  });

  it("no reason → renders nothing (the API never sends a chip without one)", () => {
    const { container } = draw(attentionHorseFixture({ reason: null }));
    expect(container.firstChild).toBeNull();
  });
});
