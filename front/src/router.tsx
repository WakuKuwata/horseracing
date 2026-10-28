import { createBrowserRouter, Navigate } from "react-router-dom";

import App from "./App";
import { AttentionPage } from "./pages/AttentionPage";
import { HorseDetailPage } from "./pages/HorseDetailPage";
import { JockeyDetailPage } from "./pages/JockeyDetailPage";
import { MarketEvProspectivePage } from "./pages/MarketEvProspectivePage";
import { RaceDetailPage } from "./pages/RaceDetailPage";
import { PurchaseComparisonPage } from "./pages/PurchaseComparisonPage";
import { PurchaseListPage } from "./pages/PurchaseListPage";
import { RaceListPage } from "./pages/RaceListPage";

export const routes = [
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <RaceListPage /> },
      { path: "races/:raceId", element: <RaceDetailPage /> },
      { path: "horses/:horseId", element: <HorseDetailPage /> },
      { path: "jockeys/:jockeyId", element: <JockeyDetailPage /> },
      // B12 (2026-10-28): the 065 shadow-log page is closed (its collection path never ran and its
      // generation is the legacy policy, comparison-only since 154); the prospective record of the
      // 期待回収率 columns lives at /market-ev/prospective (B8). Old links land there.
      { path: "shadow-log", element: <Navigate to="/market-ev/prospective" replace /> },
      { path: "purchases", element: <PurchaseListPage /> },
      { path: "purchase-comparison", element: <PurchaseComparisonPage /> },
      // Feature 138: 注目条件 S1〜S5 の一覧(凍結した過去検証 + 前向き検証の現況)
      { path: "attention", element: <AttentionPage /> },
      // B8: 期待回収率の列の前向き現況(発走前オッズ・公式払戻)
      { path: "market-ev/prospective", element: <MarketEvProspectivePage /> },
    ],
  },
];

export const router = createBrowserRouter(routes, {
  future: { v7_relativeSplatPath: true },
});
