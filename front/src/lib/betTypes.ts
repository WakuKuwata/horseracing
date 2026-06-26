// JRA bet types (English keys used by the API) with Japanese labels for display.
export const BET_TYPES = [
  { key: "win", label: "単勝" },
  { key: "place", label: "複勝" },
  { key: "quinella", label: "馬連" },
  { key: "exacta", label: "馬単" },
  { key: "wide", label: "ワイド" },
  { key: "trio", label: "三連複" },
  { key: "trifecta", label: "三連単" },
] as const;

export type BetTypeKey = (typeof BET_TYPES)[number]["key"];

// Exotic bet types (used by joint-probability and estimated-odds endpoints, which require ?bet_type).
export const EXOTIC_BET_TYPES = BET_TYPES.filter((b) => b.key !== "win");

export function betTypeLabel(key: string): string {
  return BET_TYPES.find((b) => b.key === key)?.label ?? key;
}
