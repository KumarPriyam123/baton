/**
 * Two people, two browsers, one item (BUILD_PLAN phase 10). Each test makes its own request so
 * nothing depends on seed data or on the other test.
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

/** Raises a Payments request through the API as whoever `page` is signed in as. */
async function createRequest(page: Page, title: string, description: string): Promise<string> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "baton_csrf")?.value ?? "";
  const response = await page.request.post("/api/v1/items", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    data: { team_key: "PAY", type: "ops_task", title, description, priority: 2 },
  });
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { key: string }).key;
}

function ownerOf(page: Page) {
  return page.locator("dt", { hasText: /^Owner$/ }).locator("xpath=following-sibling::dd[1]");
}

test("claim race: both click Assign to me, one wins, the other is told who took it, both show one owner", async ({
  browser,
}) => {
  const priya = await signInAs(browser, "Priya Nair");
  const key = await createRequest(priya.page, `E2E claim race ${String(Date.now())}`, "Race me.");

  const asha = await signInAs(browser, "Asha Rao");
  const rahul = await signInAs(browser, "Rahul Verma");
  for (const { page } of [asha, rahul]) {
    await page.goto(`/items/${key}`);
    await expect(
      page.getByRole("group", { name: "Actions" }).getByRole("button", { name: "Assign to me" }),
    ).toBeVisible();
  }

  // Released together: neither click waits for the other's answer.
  await Promise.all(
    [asha, rahul].map(({ page }) =>
      page
        .getByRole("group", { name: "Actions" })
        .getByRole("button", { name: "Assign to me" })
        .click(),
    ),
  );

  const won = (page: Page) => page.getByText("Assigned to you");
  const lost = (page: Page) => page.getByText(new RegExp(`took ${key}`));
  await expect(won(asha.page).or(lost(asha.page))).toBeVisible();
  await expect(won(rahul.page).or(lost(rahul.page))).toBeVisible();

  const ashaWon = await won(asha.page).isVisible();
  const rahulWon = await won(rahul.page).isVisible();
  expect(ashaWon !== rahulWon).toBe(true); // exactly one winner
  await expect(ashaWon ? lost(rahul.page) : lost(asha.page)).toBeVisible();

  // Both screens end on the same owner, without a reload.
  const winner = ashaWon ? "Asha Rao" : "Rahul Verma";
  await expect(ownerOf(asha.page)).toContainText(winner);
  await expect(ownerOf(rahul.page)).toContainText(winner);

  for (const p of [priya, asha, rahul]) await p.ctx.close();
});

test("stale edit: A edits the description while B saves a new one, A gets the conflict dialog with a diff", async ({
  browser,
}) => {
  const a = await signInAs(browser, "Priya Nair");
  const key = await createRequest(
    a.page,
    `E2E stale edit ${String(Date.now())}`,
    "The first draft of the description.",
  );
  const b = await signInAs(browser, "Vikram Shah");

  for (const { page } of [a, b]) {
    await page.goto(`/items/${key}`);
    await expect(page.getByTestId("description")).toContainText("The first draft");
  }

  // A starts editing and types, but does not save yet.
  await a.page.getByRole("button", { name: "Edit" }).click();
  const aBox = a.page.getByRole("textbox", { name: "Description" });
  await aBox.fill("The first draft of the description, plus a sentence from A.");

  // B saves a different description first.
  await b.page.getByRole("button", { name: "Edit" }).click();
  await b.page
    .getByRole("textbox", { name: "Description" })
    .fill("A completely different text from B.");
  await b.page.getByRole("button", { name: "Save description" }).click();
  await expect(b.page.getByText("Saved", { exact: true })).toBeVisible();

  // A saves from the old view: the server answers 412, the same field changed.
  await a.page.getByRole("button", { name: "Save description" }).click();
  const dialog = a.page.getByRole("dialog");
  await expect(dialog.getByText("This request changed while you were editing")).toBeVisible();
  await expect(dialog.getByTestId("diff")).toBeVisible();
  await expect(dialog.getByTestId("diff").locator("del", { hasText: "different" })).toBeVisible();
  await expect(dialog.getByTestId("diff").locator("ins", { hasText: "sentence" })).toBeVisible();

  // The draft survived, and A can choose.
  await dialog.getByRole("button", { name: "Save my version" }).click();
  await expect(a.page.getByTestId("description")).toContainText("plus a sentence from A");

  for (const p of [a, b]) await p.ctx.close();
});
