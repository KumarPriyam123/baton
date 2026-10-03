// Light-theme 1280 px screenshots for design review: node scripts/screenshots.mjs
// BASE_URL (default http://localhost:8080), PW_CHANNEL=chrome to use an installed Chrome,
// AS="Priya Nair" to pick the demo account, THEME=dark for the night desk.
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
await page.screenshot({ path: `${out}/login-${theme}.png` });
await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
await page.waitForURL(/\/inbox/);
await page.getByRole("heading", { level: 2 }).first().waitFor();
await page.waitForTimeout(400);
await page.screenshot({ path: `${out}/inbox-${theme}.png` });

await page.goto(
  `${base}/items?status=new&status=in_progress&status=blocked&status=awaiting_approval`,
);
await page.locator("[data-strip]").first().waitFor();
await page.locator("[data-strip]").first().click();
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/queue-${theme}.png` });

await page.getByRole("button", { name: "New request" }).click();
await page.getByLabel("Title").fill("Refund stuck for order");
await page.waitForTimeout(900);
await page.screenshot({ path: `${out}/new-request-${theme}.png` });

await browser.close();
console.log("saved to", out);
