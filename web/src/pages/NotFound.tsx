import { Link } from "react-router";

export function NotFound() {
  return (
    <section>
      <h1>Page not found</h1>
      <p>
        There is nothing at this address. <Link to="/">Start a new translation</Link>.
      </p>
    </section>
  );
}
