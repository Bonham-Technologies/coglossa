import { Link, useRouteError } from "react-router";
import { ErrorMessage } from "../components/ErrorMessage";

/** Shown when a page throws while rendering, in place of the page: the layout's top bar and
 * navigation stay (routes.tsx), and the error is named. */
export function RouteError() {
  const error = useRouteError();
  return (
    <section>
      <h1>Something went wrong</h1>
      <ErrorMessage error={error} />
      <p>
        <Link to="/">Start a new translation</Link>
      </p>
    </section>
  );
}
