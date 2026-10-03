// 1280 px screenshots of the phase 11 screens: node scripts/management-screens.mjs
// BASE_URL (default http://localhost:8080), PW_CHANNEL=chrome, AS="Priya Nair", THEME=dark.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.BASE_URL ?? "http://localhost:8080";
const who = process.env.AS ?? "Priya Nair";
const theme = process.env.THEME ?? "light";
const out = "test-results/screens";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch(
  process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {},
);
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  colorScheme: theme === "dark" ? "dark" : "light",
});
const page = await context.newPage();

await page.goto(`${base}/login`);
await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
await page.waitForURL(/\/inbox/);

await page.goto(`${base}/dashboard`);
await page.getByRole("table").waitFor();
await page
  .getByRole("button", { name: /oldest items and load per owner/ })
  .first()
  .click();
await page.waitForTimeout(400);
await page.screenshot({ path: `${out}/dashboard-${theme}.png` });

if (theme === "light") {
  await page.goto(`${base}/decisions`);
  await page.getByRole("listitem").first().waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/decisions-${theme}.png` });

  await page.getByRole("button", { name: /^Notifications/ }).click();
  await page.getByRole("dialog", { name: "Notifications" }).waitFor();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/notifications-${theme}.png` });
}

await browser.close();
console.log("saved to", out);
