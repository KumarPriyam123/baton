/**
 * Live updates over SSE, through nginx (BUILD_PLAN phase 7). The old 10 s poll could not pass the
 * 2 s limit below, so a pass means the stream delivered the change.
 */
import { type Browser, type BrowserContext, type Page, expect, test } from "@playwright/test";

async function signInAs(
  browser: Browser,
  name: string,
): Promise<{ page: Page; ctx: BrowserContext }> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1100 } });
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(`Sign in as ${name}`) }).click();
  await expect(page).toHaveURL(/\/inbox/);
  return { page, ctx };
}

async function csrfOf(page: Page): Promise<string> {
  return (await page.context().cookies()).find((c) => c.name === "baton_csrf")?.value ?? "";
}

test("B changes the priority and A's open detail shows it within 2 s, without a reload", async ({
  browser,
}) => {
  const a = await signInAs(browser, "Priya Nair");
  const created = await a.page.request.post("/api/v1/items", {
    headers: { "X-CSRF-Token": await csrfOf(a.page), "Idempotency-Key": crypto.randomUUID() },
    data: {
      team_key: "PAY",
      type: "ops_task",
      title: `E2E live priority ${String(Date.now())}`,
      description: "Watch me change.",
      priority: 2,
    },
  });
  expect(created.ok()).toBe(true);
  const item = (await created.json()) as { key: string; version: number };

  const b = await signInAs(browser, "Vikram Shah");
  await a.page.goto(`/items/${item.key}`);
  await expect(a.page.getByRole("button", { name: /Priority P2, change/ })).toBeVisible();

  let reloaded = false;
  a.page.on("load", () => {
    reloaded = true;
  });

  const changed = await b.page.request.patch(`/api/v1/items/${item.key}`, {
    headers: { "X-CSRF-Token": await csrfOf(b.page), "If-Match": `"${String(item.version)}"` },
    data: { priority: 1 },
  });
  expect(changed.ok()).toBe(true);

  await expect(a.page.getByRole("button", { name: /Priority P1, change/ })).toBeVisible({
    timeout: 2_000,
  });
  expect(reloaded).toBe(false);

  for (const person of [a, b]) await person.ctx.close();
});
