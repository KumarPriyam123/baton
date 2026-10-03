// Item detail screenshots for design review: node scripts/detail-screens.mjs
// BASE_URL, PW_CHANNEL=chrome, AS="Priya Nair", THEME=dark, KEYS="PAY-1:new,PAY-2:blocked"
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.BASE_URL ?? "http://localhost:8080";
const who = process.env.AS ?? "Priya Nair";
const theme = process.env.THEME ?? "light";
const targets = (process.env.KEYS ?? "")
  .split(",")
  .filter(Boolean)
  .map((t) => t.split(":"));
const out = "test-results/screens";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch(
  process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {},
);
const context = await browser.newContext({
  viewport: { width: 1280, height: 1700 },
  colorScheme: theme === "dark" ? "dark" : "light",
});
const page = await context.newPage();
await page.goto(`${base}/login`);
await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
await page.waitForURL(/\/inbox/);

for (const [key, label] of targets) {
  await page.goto(`${base}/items/${key}`);
  await page.getByRole("heading", { name: "Timeline" }).waitFor();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/detail-${label}-${theme}.png` });
}
await browser.close();
console.log("saved to", out);
