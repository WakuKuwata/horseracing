import { ShadowLogPanel } from "../components/ShadowLogPanel";

/**
 * Feature 065: dedicated page for the prospective shadow-betting log — the honest instrument that
 * measures realized return on the frozen, actually-bettable pre-race odds (NOT closing). Kept on its
 * own route so it is never confused with the retrospective closing backtest on the race-detail view.
 */
export function ShadowLogPage() {
  return (
    <main className="page">
      <ShadowLogPanel />
    </main>
  );
}
