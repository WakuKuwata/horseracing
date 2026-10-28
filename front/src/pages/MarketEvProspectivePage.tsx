import { MarketEvProspectivePanel } from "../components/MarketEvProspectivePanel";

/**
 * B8 (2026-10-28): dedicated page `/market-ev/prospective` — how much prospective evidence the two
 * 期待回収率 columns have gathered on genuine pre-race quotes, settled at the official win payout.
 * Its own route so the record is never read as part of a race's page.
 */
export function MarketEvProspectivePage() {
  return (
    <main className="page" data-testid="market-ev-prospective-page">
      <MarketEvProspectivePanel />
    </main>
  );
}
