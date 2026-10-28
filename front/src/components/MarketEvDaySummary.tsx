import type { ErrorInfo } from "../api/client";
import type { MarketEvDayResponse } from "../api/types";

/**
 * B7 (2026-10-28): one neutral line above the day board — how many of the day's races have a
 * stored 期待回収率 and how many of those have a horse above either threshold. Counts and state
 * only (021 規律): the board keeps its venue/race-number order, nothing is ranked by the value,
 * no colour, no per-horse figure. Loading and failure are quiet notes (the board is the page).
 */
export function MarketEvDaySummary({
  day,
  isLoading,
  error,
}: {
  day: MarketEvDayResponse | undefined;
  isLoading: boolean;
  error: ErrorInfo | null;
}) {
  if (error) {
    return (
      <p className="note day-ev-summary" data-testid="market-ev-day-error">
        期待回収率の一覧を取得できませんでした(HTTP {error.status} {error.code})
      </p>
    );
  }
  if (isLoading || !day) {
    return (
      <p className="note day-ev-summary" data-testid="market-ev-day-loading">
        期待回収率の一覧を読み込み中…
      </p>
    );
  }
  const pending = day.n_computed - day.n_available;
  const withAbove = day.items.filter((i) => (i.n_exceeds_threshold ?? 0) > 0).length;
  const withMixAbove = day.items.filter((i) => (i.n_exceeds_mix_threshold ?? 0) > 0).length;
  return (
    <p className="note day-ev-summary" data-testid="market-ev-day-summary">
      期待回収率({day.model_version}): 計算済み {day.n_available} / {day.n_races} レース
      {pending > 0 ? `(ほかに再計算待ちなど ${pending} レース)` : ""}
      ・120%超の馬がいるレース {withAbove}・市場混合100%超の馬がいるレース {withMixAbove}。
      並びは会場・レース番号のままで、期待回収率では並べ替えません。値は推定で、実績ではありません。
    </p>
  );
}
