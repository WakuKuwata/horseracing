import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { CoveragePage } from "./pages/CoveragePage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { JobsPage } from "./pages/JobsPage";
import { ModelDetailPage } from "./pages/ModelDetailPage";
import { ModelRegistryPage } from "./pages/ModelRegistryPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <ModelRegistryPage /> },
      { path: "models/:modelVersion", element: <ModelDetailPage /> },
      { path: "coverage", element: <CoveragePage /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "diagnostics", element: <DiagnosticsPage /> },
    ],
  },
]);
