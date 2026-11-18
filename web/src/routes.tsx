import { Navigate, type RouteObject } from "react-router";
import { Layout } from "./components/Layout";
import { NotFound } from "./pages/NotFound";
import { RouteError } from "./pages/RouteError";
import { ProjectRedirect, RunRedirect } from "./set/Redirects";
import { SetPage } from "./set/SetPage";
import { SettingsPage } from "./settings/SettingsPage";
import { NewTranslationPage } from "./translate/NewTranslationPage";

/** Pages go in the frame route's children, before the catch-all. */
export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    errorElement: <RouteError />, // the layout itself failed: no frame to keep
    children: [
      {
        // A page that throws while rendering is replaced inside the layout: the top bar stays.
        errorElement: <RouteError />,
        children: [
          { index: true, element: <NewTranslationPage /> },
          { path: "t/:projectId/:runId?", element: <SetPage /> },
          { path: "settings", element: <SettingsPage /> },
          // spec §3.2: old addresses keep working
          { path: "projects", element: <Navigate to="/" replace /> },
          { path: "projects/:projectId", element: <ProjectRedirect /> },
          { path: "projects/:projectId/guide", element: <ProjectRedirect /> },
          { path: "runs/:runId/*", element: <RunRedirect /> },
          { path: "system", element: <Navigate to="/settings" replace /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];
