import { defineConfig, devices } from "@playwright/test";

const PORT = 8765;
// CGL-26: a second stack that signs people in by X-Remote-User, with teams from e2e/people.yaml
// (roles.spec.ts). The first stays open (auth none): browser downloads carry no extra headers.
export const ROLES_PORT = 8766;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // one stack, one database
  timeout: 90_000,
  expect: { timeout: 30_000 }, // runs take a few seconds against the fake models
  reporter: process.env["CI"] ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd ../api && uv run python devstack.py --host 127.0.0.1 --port ${PORT} --web-dir ../web/dist`,
      url: `http://127.0.0.1:${PORT}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    {
      command: `cd ../api && uv run python devstack.py --host 127.0.0.1 --port ${ROLES_PORT} --web-dir ../web/dist --people ../web/e2e/people.yaml`,
      url: `http://127.0.0.1:${ROLES_PORT}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
  ],
});
