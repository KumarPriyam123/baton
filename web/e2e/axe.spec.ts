import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Serious and critical axe violations on the main screens (light theme, 1280 px).
// Run with the stack up and the demo seed: BASE_URL=http://localhost:8080 npx playwright test axe

async function signIn(page: Page, who: string): Promise<void> {
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(`Sign in as ${who}`) }).click();
  await page.waitForURL(/\/inbox/);
}

async function serious(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map(
      (v) => `${v.id} (${String(v.impact)}): ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`,
    );
}

test("login has no serious or critical axe violations", async ({ page }) => {
  await page.goto("/login");
  await page
    .getByRole("button", { name: /Sign in as/ })
    .first()
    .waitFor();
  expect(await serious(page)).toEqual([]);
});

test.describe("signed in", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, "Priya Nair");
  });

  test("inbox", async ({ page }) => {
    await page.locator("[data-strip]").first().waitFor();
    expect(await serious(page)).toEqual([]);
  });

  test("queue with an item open", async ({ page }) => {
    await page.goto("/items?status=new&status=in_progress&status=blocked&status=awaiting_approval");
    await page.locator("[data-strip]").first().click();
    await page.getByRole("heading", { level: 1 }).first().waitFor();
    expect(await serious(page)).toEqual([]);
  });

  test("item detail page", async ({ page }) => {
    await page.goto("/items/PAY-10");
    await page.getByRole("heading", { name: "Timeline" }).waitFor();
    expect(await serious(page)).toEqual([]);
  });

  test("dashboard", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("heading", { name: "Dashboard" }).waitFor();
    await page.getByText("Payments").first().waitFor();
    expect(await serious(page)).toEqual([]);
  });
});
