# coglossa.com

The public site for Coglossa: one static page (plain HTML and CSS, no JavaScript) with the
writeup, a recorded walkthrough and sample output from a real run. Its own Vercel project
deploys it, with `site/` as the root directory; nothing else in the repository is part of it.

| Path | What it is |
|---|---|
| `public/` | Everything that is served (`vercel.json` sets it as the output directory) |
| `public/index.html`, `public/styles.css` | The page and its styles; colours are tokens with a light and a dark theme |
| `public/assets/demo/` | The walkthrough (WebM, with captions) and the screenshots (WebP) |
| `public/sample/` | The run's quality report (`report.json`, sanitised) and its web-page export |
| `public/fonts/` | Instrument Serif, DM Sans and Geist Mono (Latin subsets, SIL Open Font Licence) |
| `vercel.json` | Security headers, the CSP, clean URLs, and the Ignored Build Step |
| `check.mjs` | The pre-launch check (below) |
| `tools/` | How the demo and the fixed images are made (below); not deployed |

## Check

```bash
cd site
pnpm install --frozen-lockfile   # axe-core and playwright-core, pinned; nothing else
node check.mjs                   # node check.mjs --launch also fails on owner placeholders
```

It checks that every internal link, fragment and asset resolves; that each page has its title,
description and `lang`, and the home page its canonical URL, Open Graph tags and a 1200 × 630
OG image; that the home page has one JSON-LD block (`<script type="application/ld+json">`, data
the browser never runs, so the CSP needs nothing for it) that parses and describes Bonham
Technologies (`Organization`) and Coglossa (`SoftwareApplication`, publisher, Apache-2.0
licence) (CGL-85); that there is no other script, inline style or event handler; that
`vercel.json` sends HSTS, nosniff, Referrer-Policy, Permissions-Policy and COOP, a CSP with no
`unsafe-inline` or `unsafe-eval` for scripts, and no `X-Robots-Tag`; that `robots.txt` does not
disallow the site (only the private preview does that); that the export's one inline `<style>` matches the hash its CSP
allows; that the sample passage on the page still matches `sample/report.json`; that nothing
links to GitHub yet (CGL-78); and that `public/` stays under 3 MB. Then it serves `public/` the
way Vercel does (clean URLs and the headers from `vercel.json`) and, in Chromium at 1280 px and
360 px and in both themes, runs axe-core (0 violations), checks there is no sideways scrolling,
and fails on any failed request or CSP violation.

Chromium: `CHROMIUM=/path/to/chrome`, else `/opt/pw-browsers/chromium` when it exists, else
Playwright's own (`pnpm exec playwright-core install chromium`).

## Owner steps before launch

1. **Vercel project.** In the team that already holds the hosted preview, add a project from
   this repository with **Root Directory** `site`, framework preset **Other**, and leave the
   build, install and output settings empty: `vercel.json` sets them (nothing is installed or
   built; `public/` is served).
   - **Production branch:** Settings ▸ Git ▸ Production Branch `main`, and deploy production
     from `main` only.
   - **Git Fork Protection:** Settings ▸ Git ▸ Git Fork Protection on, so a pull request from a
     fork never deploys without approval.
   - **No secrets and no environment variables.** The site needs none; add none.
   - **Ignored Build Step:** leave it as configured. `vercel.json`'s
     `git diff --quiet HEAD^ HEAD -- .` skips a deploy unless the commit changed `site/` (the
     command runs in the root directory, so `.` is `site/`). Exit 0 skips the deploy; any
     other exit (a change, or no `HEAD^` to compare with) builds it. If the first production
     deployment is cancelled because its commit didn't touch `site/`, redeploy it from
     Deployments ▸ ⋯ ▸ Redeploy with **Use project's Ignore Build Step** unchecked.
2. **Domains.** Settings ▸ Domains: add `coglossa.com` and `www.coglossa.com`, and set `www`
   to redirect (308) to `coglossa.com`, the canonical URL.
3. **DNS at Porkbun.** Enter the records Vercel shows for each domain (usually an `A` record
   for the apex and a `CNAME` for `www`), and delete Porkbun's default parking records. If the
   domain has CAA records, allow the certificate authority Vercel names.
4. **Registrar lock.** At Porkbun, turn on the domain's transfer lock, and auto-renew.
5. **Accounts.** Passkeys or 2FA on GitHub, Vercel and Porkbun, for everyone with access.
6. **GitHub Actions.** When the repository goes public (CGL-78): Settings ▸ Actions ▸ General ▸
   require approval for workflows from outside contributors (all outside collaborators).
7. **Vercel plan.** Hobby is for non-commercial use only. Confirm the team the project lands in
   is on Pro (the hosted preview's README says that team is), or move it to one that is.
8. **Before you share the link,** read "Owner checks" below and run `node check.mjs --launch`.

## Owner checks

- **Licence.** The page says "Coglossa will be released under the Apache License 2.0." The
  repository's `LICENSE` is still proprietary until CGL-77 lands; change the sentence then.
- **Contact.** The page gives `support@bonham.tech`, as Corelata's and Unvarnish's sites do (owner,
  2026-11-15: mail and contact for the product domains go through bonham.tech). So does
  `public/.well-known/security.txt`, whose `Expires:` must stay under a year out: move it
  before 2027-11-01.
- **The demo uses the practice models.** They repeat the source with a language tag, and the
  page says so. A recording from real models would read better: run the capture against a
  stack whose config points at real model servers.
- **The hosted preview.** The page says it exists for demonstrations, with accounts by
  invitation, and uses hosted models.
- **Structured data.** The JSON-LD in `index.html` gives Bonham Technologies the logo
  `apple-touch-icon.png`, which is Coglossa's mark: the site has no Bonham logo. Point `logo` at
  a Bonham logo (112 × 112 or larger) when there is one. It has no `offers`: the README states
  the licence but no price; add a price-0 offer only once that is a claim you want to make.
- **HSTS** is two years with `includeSubDomains`, without `preload`: preloading is hard to undo.

## Regenerating the demo

The demo is captured from a real local Coglossa with the practice models, from the repository
root:

```bash
cd web && pnpm install --frozen-lockfile && pnpm build && cd ..
cd api && COGLOSSA_TEST_DATABASE_URL=... uv run python devstack.py --port 8781 --web-dir ../web/dist
```

Then, in `site/`, with the stack running:

```bash
node tools/capture-demo.mjs          # screenshots, the raw video and the exports, in tools/.raw and tools/.exports
tools/optimise.sh tools/.raw         # WebP screenshots and a small VP9 WebM in public/assets/demo/ (needs ffmpeg)
node tools/sanitise-report.mjs       # public/sample/: the report without prompts, model calls or config
node tools/make-images.mjs           # only when the OG card or the icon changes
node check.mjs
```

- `capture-demo.mjs` translates the same short story each time, opens the flagged passage, edits
  it, adds "always translate Kerlann as Kerlann" and opens the guide and the download menu. It
  deletes the sets it made, so the sidebar starts empty.
- Video needs the ffmpeg build of the Playwright version in use. If Playwright reports it
  missing, run `pnpm exec playwright-core install ffmpeg`, or point `PLAYWRIGHT_MODULE` at a
  Playwright package whose ffmpeg is installed (for example
  `PLAYWRIGHT_MODULE=/opt/node22/lib/node_modules/playwright/index.mjs`).
- After a new capture, update the caption times in `public/assets/demo/walkthrough.vtt` if the
  steps moved, and the sample passage in `index.html` if the report changed (`check.mjs` tells
  you). If the app's web-page export changes its inline style, update the hash in
  `vercel.json` (`check.mjs` prints the one it needs).
