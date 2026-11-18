import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { makeQueryClient, Providers } from "../Providers";
import { routes } from "../routes";

/** Render the whole app at `path`, as a browser would after loading that URL. */
export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = makeQueryClient({ retry: false });
  const user = userEvent.setup();
  render(
    <Providers client={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, user, queryClient };
}

/** For hook tests: a fresh query client and a wrapper that provides it. */
export function queryWrapper() {
  const queryClient = makeQueryClient({ retry: false });
  function wrapper({ children }: { children: ReactNode }) {
    return <Providers client={queryClient}>{children}</Providers>;
  }
  return { queryClient, wrapper };
}

/** Render one element at `path`, matched by `pattern` so useParams works. Any other address
 * renders "Somewhere else", so tests can follow a navigation through router.state. */
export function renderAt(
  element: ReactElement,
  { path = "/", pattern = "*" }: { path?: string; pattern?: string } = {},
) {
  const elsewhere = { path: "*", element: <p>Somewhere else</p> };
  const router = createMemoryRouter(
    pattern === "*" ? [{ path: "*", element }] : [{ path: pattern, element }, elsewhere],
    { initialEntries: [path] },
  );
  const queryClient = makeQueryClient({ retry: false });
  const user = userEvent.setup();
  const view = render(
    <Providers client={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, user, queryClient, ...view };
}
