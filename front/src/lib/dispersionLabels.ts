/**
 * Feature 066: single source of truth for dispersion band → Japanese display label.
 *
 * NEUTRAL descriptions of how concentrated the market's win probabilities are (堅い ↔ 波乱含み).
 * NO profit/danger/value semantics — this is a "how readable is the race" readout, not a buy signal.
 */
import type { RaceDispersion } from "../api/types";

type Band = NonNullable<RaceDispersion["band"]>;

export const BAND_LABEL: Record<Band, string> = {
  firm: "堅い",
  somewhat_firm: "やや堅い",
  standard: "標準",
  somewhat_open: "やや波乱",
  open: "波乱含み",
};

type Reason = NonNullable<RaceDispersion["unavailable_reason"]>;

export const UNAVAILABLE_LABEL: Record<Reason, string> = {
  no_market_odds: "市場オッズが無いため、荒れ度は表示できません。",
  partial_market_odds: "一部の出走馬に市場オッズが無いため、荒れ度は表示できません。",
};
