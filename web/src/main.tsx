import "./fonts";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/translate.css";
import "./styles/review.css";
import "./styles/guide.css";
import { applyTheme } from "./theme/theme";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { makeQueryClient, Providers } from "./Providers";
import { routes } from "./routes";

applyTheme();
// The other scripts' faces, in their own stylesheet that does not hold up first paint (CGL-39).
// If it cannot load (say a tab left open across an upgrade), those scripts show in a system font
// and the app carries on, so the failure is not left as an unhandled rejection.
import("./script-fonts").catch(() => {});

const router = createBrowserRouter(routes);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Providers client={makeQueryClient()}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
