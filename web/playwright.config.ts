import { defineConfig } from "@playwright/test";

// BASE_URL: where the stack is served (nginx). PW_CHANNEL=chrome uses an installed Chrome instead
// of Playwright's own download, handy on a laptop; CI and the e2e container use the bundled one.
export default defineConfig({
  testDir: "e2e",
  reporter: "list",
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8080",
    viewport: { width: 1280, height: 800 },
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  },
});
