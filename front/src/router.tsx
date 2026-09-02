import { createBrowserRouter } from "react-router-dom";

import App from "./App";
import { HorseDetailPage } from "./pages/HorseDetailPage";
import { JockeyDetailPage } from "./pages/JockeyDetailPage";
import { RaceDetailPage } from "./pages/RaceDetailPage";
import { PurchaseComparisonPage } from "./pages/PurchaseComparisonPage";
import { PurchaseListPage } from "./pages/PurchaseListPage";
import { RaceListPage } from "./pages/RaceListPage";
import { ShadowLogPage } from "./pages/ShadowLogPage";

export const routes = [
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <RaceListPage /> },
      { path: "races/:raceId", element: <RaceDetailPage /> },
      { path: "horses/:horseId", element: <HorseDetailPage /> },
      { path: "jockeys/:jockeyId", element: <JockeyDetailPage /> },
      { path: "shadow-log", element: <ShadowLogPage /> },
      { path: "purchases", element: <PurchaseListPage /> },
      { path: "purchase-comparison", element: <PurchaseComparisonPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes, {
  future: { v7_relativeSplatPath: true },
});
