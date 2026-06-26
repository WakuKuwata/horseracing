import { createBrowserRouter } from "react-router-dom";

import App from "./App";
import { RaceDetailPage } from "./pages/RaceDetailPage";
import { RaceListPage } from "./pages/RaceListPage";

export const routes = [
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <RaceListPage /> },
      { path: "races/:raceId", element: <RaceDetailPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes, {
  future: { v7_relativeSplatPath: true },
});
