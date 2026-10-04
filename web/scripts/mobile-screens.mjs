/* global document */
// 375 px screenshots of Inbox, Queue and Item detail: node scripts/mobile-screens.mjs
// BASE_URL (default http://localhost:5199), PW_CHANNEL=chrome, AS="Priya Nair", THEME=dark.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.BASE_URL ?? "http://localhost:5199";
const who = process.env.AS ?? "Priya Nair";
const theme = process.env.THEME ?? "light";
const out = "test-results/screens";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch(
  process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {},
);
const context = await browser.newContext({
  viewport: { width: 375, height: 800 },
  colorScheme: theme === "dark" ? "dark" : "light",
});
const page = await context.newPage();
await page.goto(`${base}/login`);
await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
await page.waitForURL(/\/inbox/);
await page.waitForTimeout(1200);
await page.screenshot({ path: `${out}/m-inbox-${theme}.png` });

await page.goto(`${base}/items`);
const first = page.locator("[data-strip]").first();
await first.waitFor();
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/m-queue-${theme}.png` });
const href = await first.getAttribute("href");

await page.goto(`${base}${href}`);
await page.getByRole("heading", { name: "Timeline" }).waitFor();
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/m-item-${theme}.png` });
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);
console.log("horizontal overflow px on item page:", overflow);
await browser.close();
