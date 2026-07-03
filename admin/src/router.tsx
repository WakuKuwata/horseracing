import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { ModelDetailPage } from "./pages/ModelDetailPage";
import { ModelRegistryPage } from "./pages/ModelRegistryPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <ModelRegistryPage /> },
      { path: "models/:modelVersion", element: <ModelDetailPage /> },
    ],
  },
]);
