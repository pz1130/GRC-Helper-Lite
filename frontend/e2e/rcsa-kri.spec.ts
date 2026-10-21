import { expect, test } from "@playwright/test";
import { LEAD, mockApi } from "./rcsa-mocks";

const RULE = {
  id: 3, name: "Acme overdue reviews", source: "Access feed", unit: "count", control_id: 9, implementation_id: 11,
  target_snapshot: { control: { id: 9, code: "C-0009", title: "Access review" }, implementation: {
    id: 11, description: "Review system access", owner_user_id: 2, status: "implemented", how_enforced: "manual",
    asset: { id: 1, name: "Acme Ledger", environment: "prod", owner_user_id: 2 },
  } },
  direction: "high", amber_threshold: "5", red_threshold: "10", owner_id: 1, decision_days: 7,
  enabled: false, version: 1, latest: null,
};

const OBSERVATION = {
  id: 1, external_id: "acme-1", observed_at: "2026-01-01T10:00:00Z", received_at: "2026-01-01T10:01:00Z",
  value: "12.000000", source_ref: "ACME-1", band: "red", trigger_id: 9,
};

test("create a disabled KRI for a selected system", async ({ page }) => {
  let created: Record<string, unknown> | undefined;
  await mockApi(page, LEAD, {
    "/api/controls": (route) => route.fulfill({ json: [{ id: 9, code: "C-0009", title: "Access review" }] }),
    "/api/rcsa/kri-targets": (route) => route.fulfill({ json: [RULE.target_snapshot.implementation] }),
    "/api/rcsa/kris": (route) => {
      if (route.request().method() === "POST") {
        created = route.request().postDataJSON();
        return route.fulfill({ json: RULE });
      }
      return route.fulfill({ json: { items: [], total: 0 } });
    },
    "/api/rcsa/kris/3": (route) => route.fulfill({ json: RULE }),
    "/api/rcsa/kris/3/observations": (route) => route.fulfill({ json: { items: [], total: 0 } }),
  });
  await page.goto("/rcsa/kris");
  await page.getByLabel("Indicator name", { exact: true }).fill(RULE.name);
  await page.getByLabel("Data source", { exact: true }).fill(RULE.source);
  await page.getByLabel("Unit", { exact: true }).fill(RULE.unit);
  await page.getByRole("combobox", { name: "Control", exact: true }).selectOption("9");
  await page.getByRole("combobox", { name: "Control implementation", exact: true }).selectOption("11");
  await page.getByLabel("Amber threshold (inclusive)", { exact: true }).fill("5");
  await page.getByLabel("Red threshold (inclusive)", { exact: true }).fill("10");
  await page.getByRole("button", { name: "Create indicator", exact: true }).click();
  await expect.poll(() => created?.implementation_id).toBe(11);
  expect(created).toMatchObject({ amber_threshold: "5", red_threshold: "10", control_id: 9 });
  await expect(page).toHaveURL(/\/rcsa\/kris\/3$/);
  await expect(page.getByRole("checkbox", { name: "Enable indicator" })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Record observation", exact: true })).toBeDisabled();
});

test("enable an indicator and record a red observation with its review event", async ({ page }) => {
  let rule = { ...RULE };
  let observation: Record<string, unknown> | undefined;
  await mockApi(page, LEAD, {
    "/api/rcsa/kris/3": (route) => {
      if (route.request().method() === "PATCH") rule = { ...rule, ...route.request().postDataJSON(), version: 2 };
      return route.fulfill({ json: rule });
    },
    "/api/rcsa/kris/3/observations": (route) => {
      if (route.request().method() === "POST") {
        observation = route.request().postDataJSON();
        return route.fulfill({ json: OBSERVATION });
      }
      return route.fulfill({ json: { items: observation ? [OBSERVATION] : [], total: observation ? 1 : 0 } });
    },
  });
  await page.goto("/rcsa/kris/3");
  await page.getByRole("checkbox", { name: "Enable indicator" }).check();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("button", { name: "Record observation", exact: true })).toBeEnabled();
  await page.getByLabel("Source record ID", { exact: true }).fill("acme-1");
  await page.getByLabel("Observed value", { exact: true }).fill("12");
  await page.getByLabel("Observed at", { exact: true }).fill("2026-01-01T10:00");
  await page.getByLabel("Source reference", { exact: true }).fill("ACME-1");
  await page.getByRole("button", { name: "Record observation", exact: true }).click();
  await expect.poll(() => observation?.value).toBe("12");
  await expect(page.getByRole("link", { name: "#9", exact: true })).toHaveAttribute("href", "/rcsa/triggers/9");
  await expect(page.getByText("Red", { exact: true })).toBeVisible();
});

test("a viewer sees recorded bands and dates without configuration or ingestion controls", async ({ page }) => {
  await mockApi(page, { ...LEAD, role: "viewer" }, {
    "/api/rcsa/kris/3": (route) => route.fulfill({ json: RULE }),
    "/api/rcsa/kris/3/observations": (route) => route.fulfill({ json: { items: [OBSERVATION], total: 1 } }),
  });
  await page.goto("/rcsa/kris/3");
  await expect(page.getByText("Red", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Record observation", exact: true })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "Enable indicator" })).toHaveCount(0);
  await expect(page.getByText("The band describes the latest recorded observation, not a live health check. Check its observation time.", { exact: true })).toBeVisible();
});
