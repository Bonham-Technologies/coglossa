import { useEffect, useState } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { useMe } from "../api/system";
import { keyBelongsToControl } from "../lib/keys";
import { useTheme } from "../theme/theme";
import { ErrorMessage } from "./ErrorMessage";
import { HelpDialog } from "./HelpDialog";
import { Sidebar } from "./Sidebar";

/** Spec §3.1: the header, the sidebar and the main area. */
export function Layout() {
  const me = useMe();
  const { theme, setChoice } = useTheme();
  const [help, setHelp] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const dark = theme === "dark";
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "?" && !keyBelongsToControl(event.target, event.key)) setHelp(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="shell">
      <header className="topbar">
        <div className="row">
          <button
            type="button"
            className="quiet narrow-only nav-toggle"
            aria-expanded={navOpen}
            onClick={() => setNavOpen(!navOpen)}
          >
            {/* a phone's top bar has room for the icon only; the name stays for screen readers */}
            <span aria-hidden="true">☰</span>
            <span className="visually-hidden">Your translations</span>
          </button>
          <Link to="/" className="brand">
            Coglossa
          </Link>
        </div>
        <div className="topbar-actions">
          <button
            type="button"
            className="quiet wide-only"
            aria-label={dark ? "Switch to the light theme" : "Switch to the dark theme"}
            onClick={() => setChoice(dark ? "light" : "dark")}
          >
            <span aria-hidden="true">{dark ? "☀" : "☾"}</span>
          </button>
          <button type="button" className="quiet" onClick={() => setHelp(true)}>
            Help
          </button>
          <NavLink to="/settings" className="quiet">
            Settings
          </NavLink>
          {me.data?.user ? <span className="user">{me.data.user}</span> : null}
          {me.data?.logout_url ? (
            <form method="post" action={me.data.logout_url} className="signout">
              <button type="submit" className="link">
                Sign out
              </button>
            </form>
          ) : null}
        </div>
      </header>
      {me.isError ? <ErrorMessage error={me.error} /> : null}
      <div className="workspace">
        <Sidebar open={navOpen} onNavigate={() => setNavOpen(false)} />
        <main className="main">
          <Outlet />
        </main>
      </div>
      <HelpDialog open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
