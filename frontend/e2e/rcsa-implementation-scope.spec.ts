import { expect, test } from "@playwright/test";
import { ASSESSOR, ITEM, LEAD, RESPONSE, campaign, itemRoutes, mockApi } from "./rcsa-mocks";

const implementation = (id: number, name: string) => ({
  id, description: `Review access on ${name}`, status: "implemented", how_enforced: "manual", owner_user_id: 2,
  asset: { id, name, environment: "prod", owner_user_id: 2 },
});

test("scope picker selects distinct systems under the same control", async ({ page }) => {
  let saved: Record<string, unknown> | undefined;
  await mockApi(page, LEAD, {
    "/api/rcsa/campaigns/1": (route) => route.fulfill({ json: campaign(1, { status: "draft" }) }),
    "/api/rcsa/campaigns/1/items": (route) => {
      if (route.request().method() === "POST") saved = route.request().postDataJSON();
      return route.fulfill({ json: [] });
    },
    "/api/rcsa/campaigns/1/scope": (route, url) => route.fulfill({ json: url.searchParams.get("basis") === "implementation"
      ? [implementation(11, "Acme Ledger"), implementation(12, "Acme Directory")].map((snapshot) => ({
        control_id: 9, code: "C-0009", title: "Access review", owner_user_id: 2, framework_items: ["AC-2"],
        included: false, implementation_id: snapshot.id, implementation_snapshot: snapshot,
      })) : [] }),
  });
  await page.goto("/rcsa/campaigns/1");
  await page.getByRole("combobox", { name: "Assessment scope", exact: true }).selectOption("implementation");
  await page.getByRole("checkbox", { name: /C-0009 · Acme Ledger/ }).check();
  await page.getByRole("checkbox", { name: /C-0009 · Acme Directory/ }).check();
  await page.getByRole("button", { name: "Add 2 to scope" }).click();
  await expect.poll(() => saved?.implementation_ids).toEqual([11, 12]);
  expect(saved?.control_ids).toBeUndefined();
});

test("assessment displays the frozen system and implementation description", async ({ page }) => {
  const routes = itemRoutes((route) => route.fulfill({ json: RESPONSE }));
  routes["/api/rcsa/items/7"] = (route) => route.fulfill({ json: {
    ...ITEM, implementation_id: 11, implementation_snapshot: implementation(11, "Acme Ledger"),
  } });
  await mockApi(page, ASSESSOR, routes);
  await page.goto("/rcsa/items/7");
  await expect(page.getByText("Acme Ledger · prod", { exact: false })).toBeVisible();
  await expect(page.getByText("Review access on Acme Ledger", { exact: true })).toBeVisible();
});
