# Coglossa: teams, admins and who sees which sets (CGL-26)

Status: written design for review. The user chose the scope ("Who sees which sets"), teams as
the sharing model, and "sign-in proxy, file fallback" as the source of teams; they approved the
design in chat on 2026-10-18, including hiding existing sets from non-admins until an admin
assigns a team.

## 1. Problem

Everyone who can reach Coglossa sees every set and **Settings ▸ Technical details** (installation
checks, configuration, language tiers, score search). On-premises, a sign-in proxy passes the
person's name in a header (`auth.mode: trusted_header`), but the name is only recorded with
review actions. The customer has several translation desks sharing one install; a desk's sets
should not be visible to the others, and the operator screens belong to the people who run the
install.

## 2. Goal and success criteria

- A person sees the sets of their teams, plus their own personal sets. Admins see every set.
- A set that a person cannot see behaves exactly like one that does not exist (404), in every
  list, page, download, guide, review action and live update.
- Technical details, the installation status and the configuration are for admins only.
- Teams and admins come from the sign-in proxy's group header when there is one, and from a file
  in Coglossa's config folder; no user management inside Coglossa.
- An install with `auth.mode: none` behaves exactly as today: everyone is an admin, nothing is
  filtered.
- Inside a set nothing changes: any member translates, reviews, edits the guide, adds languages
  and deletes, as everyone can today.

Out of scope:
- Per-person permissions inside a set (read-only members, reviewers who cannot delete).
- An admin screen for managing people or teams.
- Restricting the command line: it runs on the server and stays an operator tool.
- Phone layouts (CGL-25).

## 3. Who a person is

### 3.1 The viewer

Every API request resolves to a **viewer**: `name` (or none), `teams` (a set of names) and
`admin` (bool).

- `auth.mode: none`: no name, no teams, `admin = true`.
- `auth.mode: trusted_header`: `name` from `auth.header` (as today; missing → 401). Then:
  - `teams` is the union of the proxy's groups (§3.2) and the file's teams for that name (§3.3).
  - `admin` is true when `auth.admin_group` (default `coglossa-admins`) is among the proxy's
    groups or the file lists the name under `admins`. The admin group is not itself a team.

### 3.2 Groups from the proxy

New config `auth.groups_header` (default **unset**). When set, Coglossa reads that header as a
comma-separated list of group names (trimmed, empty items dropped). When unset, the header is
ignored even if a client sends it. This is the safe default: a proxy that does not set the
header cannot be bypassed by a client that sends `X-Remote-Groups: coglossa-admins`. The
operator guide states that the proxy must always set (overwrite) the header, to an empty value
if the person has no groups.

### 3.3 The people file

New config `auth.people_file` (a path, relative to the config file; default unset). YAML:

```yaml
admins: [ana, sione]
teams:
  spanish-desk: [maria, jose]
  pacific: [tevita, sione]
```

- It is read at startup and validated like the config (one-line errors; `coglossa doctor`
  reports a missing or invalid file). A changed file takes effect on restart.
- Team names are 1–64 characters of letters, digits, space, `.`, `_` and `-`. People's names
  are whatever the proxy sends in `auth.header`. Both are compared case-insensitively, wherever
  they come from. CGL-31: headers are read as UTF-8 (latin-1 when not valid UTF-8), names are
  normalised to NFC, and only A–Z ignore case, so the database (`translate()`, under any
  collation) and Python agree.

### 3.4 `/api/me`

`Me` gains `admin: bool`, `teams: list[str]` (sorted) and `max_upload_mb: int`. The upload limit
moves here because `/system/config`, where the web app read it, becomes admin-only (§5).

## 4. Which sets a person sees

### 4.1 Data

`project` gains `team: str | None` and `created_by: str | None` (migration 0005; both null for
existing sets).

A viewer can see a set when any of these holds:
- the viewer is an admin;
- the set's `team` is one of the viewer's teams;
- the set has no team, its `created_by` is the viewer's name, and the viewer has a name.

So existing sets (no team, no creator) are visible only to admins until an admin moves them into
a team (§4.3). On an `auth.mode: none` install everyone is an admin, so nothing changes there.

### 4.2 Creating a set

`POST /projects` gains `team: str | None`.
- With a team: it must be one of the viewer's teams (admins: any valid team name); otherwise 422
  "you are not in team X".
- Without a team: the set is personal (`team` null, `created_by` = the viewer's name). On an
  `auth.mode: none` install `created_by` is null, which is fine because everyone is an admin.
- `created_by` is always recorded when there is a name.

Set names stay unique across the install (the CLI addresses sets by name). The web app already
picks the next free name ("Welcome letter (2)") on a clash, so a clash with a set in another team
shows only as that suffix.

### 4.3 Moving a set

`PATCH /projects/{id}` gains an optional `team` (a team name or null for "personal to its
creator"). Changing `team` is admin-only (403 for others); renaming stays open to members. The
web app shows **Move to team…** in the set's ⋯ menu for admins: a text box with suggestions from
`GET /api/teams` (admin-only: the teams in the people file, on existing sets and the admin's own).

### 4.4 Enforcement

The rule lives in the engine, `coglossa/access.py`: `Viewer`, `can_see(viewer, project)`,
`visible(viewer)` (the same rule as SQL, used by `catalog.list_projects` and `catalog.list_runs`)
and `check_visible(s, viewer, project_id=…, document_id=…, run_id=…)`. That last one loads
run → document → project and raises `NotFoundError` with the message a missing id gets
("run <id> not found", …).

The API enforces it with **one router-level dependency**. Every `/api` route's
`run_id`, `document_id` or `project_id` path parameter is checked before the route runs. FastAPI
reads and parses the body first (a body that is not JSON answers 422 whatever the id), then the
check runs before the body is validated against the route's model. So no route can forget it,
and a malformed body never tells an outsider that the set exists. Any other `{…_id}` path
parameter must name something inside a checked run (`span_id`, `flag_id`); the route
enumeration test fails for one that does not (CGL-31). Query parameters (`GET /runs?project_id=…`) are covered by the list
filters. A test walks the app's routes and calls every one that names a set as an outsider,
expecting 404 (§7).

Admin-only routes answer 403 "only an administrator can …" for others: `/system/doctor`,
`/system/config`, `/api/teams`, and a `team` change in `PATCH /projects/{id}`. `/system/tiers`
stays open (the language picker uses it).

## 5. Web app

- **Sidebar and lists:** unchanged code; the API returns only visible sets.
- **New translation:** a **Team** dropdown when the viewer has two or more teams, with the
  teams and **Only me**; the last choice is remembered in `localStorage`. One team: that team,
  no dropdown. No teams: personal, no dropdown. Admins see their own teams the same way.
- **Set header:** when the viewer has more than one team or is an admin, the subtitle starts
  with whose set it is: the team's name ("Spanish desk · French → English"), "Only you" for
  their own personal set, "Personal to <name>" for someone else's (admins), or "No team" for a
  set from before teams.
- **⋯ menu:** **Move to team…** for admins (§4.3).
- **Settings:** Appearance for everyone. **Status** and **Technical details** only for admins.
  The upload limit comes from `/api/me`.
- **Unknown or hidden set:** the page the app already shows for a set that doesn't exist; no new
  copy.

## 6. Hosted preview

- `COGLOSSA_USERS` values may be the existing password hash string or
  `{"hash": "...", "groups": ["coglossa-admins"]}`; `add-user` gains `--groups`.
- The front door forwards `x-remote-groups` (set, never copied from the request: it is added to
  `STRIPPED_HEADERS` first).
- `deploy/hosted/coglossa.hosted.yaml`: `groups_header: X-Remote-Groups`.
- Accounts: `ci` in `coglossa-admins`; `people-first` (the demo login) in team `demo`. The
  preview's existing sets become admin-only, as everywhere.

## 7. Testing

- **Viewer:** none mode; header only; file only; union of both; admin by group and by file;
  `groups_header` unset ignores a sent header; case-insensitive names; a bad people file.
- **Visibility:** the rule's three cases and legacy sets, in SQL (`list_projects`) and in the
  checks, which must agree (one parametrised test over the same fixtures).
- **Routes:** for every route with `project_id`, `document_id` or `run_id`: an outsider gets 404
  with the same body as a missing id, a team member and an admin succeed. The enumeration test
  fails for an unchecked route.
- **Admin-only routes:** 403 for a member, 200 for an admin, 200 for everyone in none mode.
- **Create and move:** team validation, personal sets, `created_by`, move by admin, 403 for
  others.
- **Web:** the Team dropdown (two teams, one, none; remembered); Settings sections by role;
  Move to team for admins only; the upload limit from `/api/me`.
- **Hosted:** users with groups, the forwarded header, a client-sent `x-remote-groups` dropped.
- **e2e:** the dev stack runs with `trusted_header` and a people file; a translator signs in,
  makes a set, and a person in another team gets the not-found page for it.
- Every existing test keeps passing in `none` mode.

## 8. Documents to update

- `docs/operator-guide.md`: teams and admins, the people file, the groups header and the rule
  that the proxy must always set it.
- `deploy/config/coglossa.example.yaml`: commented `groups_header`, `admin_group`, `people_file`.
- `deploy/hosted/README.md`: `add-user --groups`, and who is admin on the preview.
- `README.md`: one paragraph on teams.
- The UI spec: §6.2 (Settings by role) and §4.1 (the Team dropdown).

## 9. Known limits

- A person's teams change on their next request when they come from the proxy, and on restart
  when they come from the file.
- A person removed from every team keeps their personal sets but loses their team's sets; an
  admin can move sets between teams.
- Set names are unique across teams (§4.2).
- Review history records names, not teams; a set moved to another team keeps its history.
