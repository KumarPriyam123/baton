import { type Page, expect, test } from "@playwright/test";

async function signInAs(page: Page, name: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(`Sign in as ${name}`) }).click();
  await expect(page).toHaveURL(/\/inbox/);
}

test("double-clicking Create with the first response dropped makes exactly one item", async ({
  page,
}) => {
  await signInAs(page, "Asha Rao");
  const title = `E2E double submit ${String(Date.now())}`;

  await page.getByRole("button", { name: "New request" }).click();
  await page.getByLabel("Title").fill(title);

  // The first POST reaches the server and commits, but the browser never hears back: the exact
  // failure an idempotency key exists for. Every later request passes through untouched.
  let posts = 0;
  let replayed = false;
  await page.route("**/api/v1/items", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    if (posts === 1) {
      await route.fetch();
      return route.abort("failed");
    }
    return route.continue();
  });
  page.on("response", (response) => {
    if (response.request().method() === "POST" && response.headers()["idempotent-replayed"]) {
      replayed = true;
    }
  });

  await page.getByRole("button", { name: "Create request" }).dblclick();
  await expect(page.getByText(/^Created [A-Z]+-\d+$/)).toBeVisible({ timeout: 15_000 });

  // The retry carried the same key, so the server replayed the first answer instead of creating.
  expect(posts).toBeGreaterThanOrEqual(2);
  expect(replayed).toBe(true);

  const search = await page.request.get(`/api/v1/items?q=${encodeURIComponent(title)}`);
  const body = (await search.json()) as { items: { title: string }[] };
  expect(body.items.filter((item) => item.title === title)).toHaveLength(1);

  // And it shows up in the queue.
  await page.goto(`/items?q=${encodeURIComponent(title)}`);
  await expect(page.getByRole("link", { name: new RegExp(title) })).toHaveCount(1);
});
