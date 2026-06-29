import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HorseEntry } from "../api/types";
import { renderWithProviders } from "../tests/utils";
import { HorseEntriesTable } from "./HorseEntriesTable";

const entries: HorseEntry[] = [
  { horse_id: "2020000001", horse_name: "本登録馬", horse_number: 1, entry_status: "started",
    jockey_id: "05339", jockey_name: "本登録騎手" },
  // surrogate (nk:) entities still resolve to a profile -> linked too (colon kept in the path)
  { horse_id: "nk:99999", horse_name: "サロゲート馬", horse_number: 2, entry_status: "started",
    jockey_id: "nk:88", jockey_name: "サロゲート騎手" },
  // a missing jockey_id -> plain text, no link
  { horse_id: "2020000003", horse_name: "騎手なし馬", horse_number: 3, entry_status: "started",
    jockey_name: "未定" },
];

describe("HorseEntriesTable profile links (029)", () => {
  it("links horse/jockey names (incl. nk: surrogates) and skips only null ids", () => {
    renderWithProviders(<HorseEntriesTable entries={entries} predictions={[]} />);
    // canonical ids -> links
    expect(screen.getByRole("link", { name: "本登録馬" })).toHaveAttribute(
      "href", "/horses/2020000001",
    );
    expect(screen.getByRole("link", { name: "本登録騎手" })).toHaveAttribute(
      "href", "/jockeys/05339",
    );
    // surrogate ids -> also linked (they resolve to a profile)
    expect(screen.getByRole("link", { name: "サロゲート馬" })).toHaveAttribute(
      "href", "/horses/nk:99999",
    );
    expect(screen.getByRole("link", { name: "サロゲート騎手" })).toHaveAttribute(
      "href", "/jockeys/nk:88",
    );
    // null jockey_id -> plain text, no link
    expect(screen.queryByRole("link", { name: "未定" })).toBeNull();
    expect(screen.getByText("未定")).toBeInTheDocument();
  });
});
