import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(frontendRoot, "..");
const e2ePort = process.env.DEEPSEARCHER_E2E_PORT ?? "18766";
const e2eBaseURL = `http://127.0.0.1:${e2ePort}`;
const venvPython = path.join(
  projectRoot,
  process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python",
);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: path.join(projectRoot, "output/playwright/e2e-report"),
        open: "never",
      },
    ],
    [
      "json",
      {
        outputFile: path.join(projectRoot, "output/playwright/e2e-results/results.json"),
      },
    ],
  ],
  outputDir: path.join(projectRoot, "output/playwright/e2e-results/artifacts"),
  use: {
    baseURL: e2eBaseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: `"${venvPython}" scripts/e2e_workspace_server.py`,
    cwd: projectRoot,
    url: `${e2eBaseURL}/api/health/live`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
