// 1280 px screenshots of the frontend extras: node scripts/extras-screens.mjs
// BASE_URL (default http://localhost:5199), PW_CHANNEL=chrome, THEME=dark.
// Signs in as each role it needs through the demo account buttons.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.BASE_URL ?? "http://localhost:5199";
const theme = process.env.THEME ?? "light";
const width = Number(process.env.WIDTH ?? 1280);
const out = "test-results/screens";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch(
  process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {},
);

async function signedIn(who) {
  const context = await browser.newContext({
    viewport: { width, height: 800 },
    colorScheme: theme === "dark" ? "dark" : "light",
  });
  const page = await context.newPage();
  await page.goto(`${base}/login`);
  await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
  await page.waitForURL(/\/inbox/);
  return page;
}

const shot = (page, name) => page.screenshot({ path: `${out}/${name}-${theme}-${width}.png` });

const lead = await signedIn(process.env.AS ?? "Priya Nair");
await lead.goto(`${base}/items`);
const first = lead.locator("[data-strip]").first();
await first.waitFor();
const href = await first.getAttribute("href");
await lead.goto(`${base}${href}`);
await lead.getByRole("heading", { name: "Timeline" }).waitFor();
await lead.keyboard.press("Control+k");
await lead.getByRole("dialog").waitFor();
await lead.waitForTimeout(300);
await shot(lead, "palette-open");
await lead.keyboard.type("refund");
await lead.waitForTimeout(900);
await shot(lead, "palette-search");

await lead.keyboard.press("Escape");
await lead.getByRole("button", { name: "Edit title" }).click();
await lead.waitForTimeout(300);
await shot(lead, "item-edit-title");
await lead.getByRole("button", { name: "Cancel" }).click();
await lead.getByRole("button", { name: "Change due date" }).click();
await lead.waitForTimeout(300);
await shot(lead, "item-edit-due");
await lead.keyboard.press("Escape");
await lead.getByRole("button", { name: /^Type .*change/ }).click();
await lead.waitForTimeout(300);
await shot(lead, "item-edit-type"); // never saved: the dev data is shared
await lead.keyboard.press("Escape");

const admin = await signedIn("Admin");
await admin.goto(`${base}/admin/jobs?status=dead`);
await admin.waitForTimeout(2500);
await shot(admin, "jobs-dead");
await admin.goto(`${base}/admin/jobs?status=done`);
await admin.waitForTimeout(2500);
await shot(admin, "jobs-done");
await admin.goto(`${base}/admin/jobs`);
await admin.waitForTimeout(2500);
await shot(admin, "jobs-pending");

await lead.goto(`${base}/admin/jobs`);
await lead.waitForTimeout(1500);
await shot(lead, "jobs-forbidden");
await lead.goto(`${base}/teams/PAY/settings`);
await lead.getByRole("heading", { name: "Members" }).waitFor();
await lead.waitForTimeout(800);
await shot(lead, "team-settings-lead");
await lead.getByRole("button", { name: "Remove Rahul Verma" }).click();
await lead
  .getByRole("dialog")
  .getByText(/owns|doesn't own/)
  .waitFor();
await lead.waitForTimeout(300);
await shot(lead, "team-remove-confirm");
await lead.getByRole("button", { name: "Cancel" }).click(); // never confirm: the dev data is shared

const member = await signedIn("Asha Rao");
await member.goto(`${base}/teams/PAY/settings`);
await member.getByRole("heading", { name: "Members" }).waitFor();
await member.waitForTimeout(800);
await shot(member, "team-settings-member");
await browser.close();
console.log("saved to", out);
