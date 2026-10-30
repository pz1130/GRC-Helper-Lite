import { expect, test } from "@playwright/test";
import { ASSESSOR, ITEM, LEAD, RESPONSE, itemRoutes, mockApi } from "./rcsa-mocks";

test("templates fill only empty text and leave risk scores unselected", async ({ page }) => {
  await mockApi(page, ASSESSOR, itemRoutes((route) => route.fulfill({ json: RESPONSE })));
  await page.goto("/rcsa/items/7");
  await page.getByRole("button", { name: "Add scenario", exact: true }).click();
  const form = page.getByRole("form", { name: "Add scenario" });
  await form.getByLabel("Cause", { exact: true }).fill("Existing local cause");
  await form.getByText("Optional IT risk templates", { exact: true }).click();
  await form.getByRole("combobox", { name: "Risk template", exact: true }).selectOption("r-iam-01");
  await form.getByRole("button", { name: "Fill empty scenario fields" }).click();
  await expect(form.getByLabel("Cause", { exact: true })).toHaveValue("Existing local cause");
  await expect(form.getByLabel("Event", { exact: true })).toHaveValue("Users receive or retain access beyond business need");
  await expect(form.getByLabel("Scenario key", { exact: true })).toHaveValue("r-iam-01");
  await expect(form.getByRole("combobox", { name: "Inherent likelihood", exact: true })).toHaveValue("");
  await expect(form.getByRole("combobox", { name: "Residual likelihood", exact: true })).toHaveValue("");
  await expect(form.getByText("Suggested test procedure:", { exact: false }).first()).toBeVisible();
  await expect(form.getByRole("button", { name: "Add scenario", exact: true })).toBeDisabled();
});

test("selected guides and adverse signals are saved with the answer", async ({ page }) => {
  let saved: Record<string, any> | undefined;
  const routes = itemRoutes((route) => {
    saved = route.request().postDataJSON();
    return route.fulfill({ json: { ...RESPONSE, answer: saved!.answer, version: 5 } });
  });
  await mockApi(page, ASSESSOR, routes);
  await page.goto("/rcsa/items/7");
  await page.getByText("Evidence and test guides (0 selected)", { exact: true }).click();
  await page.getByLabel("Search guides").fill("Periodic access");
  await page.getByRole("checkbox", { name: "Periodic access review", exact: true }).check();
  await expect(page.getByText("Reconcile review populations to system exports", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Add related signal" }).click();
  const signal = page.getByRole("group", { name: "Signal 1", exact: true });
  await signal.getByLabel("Source reference").fill("INC-EXAMPLE-2");
  await signal.getByLabel("Relevance to this control").fill("Access service outage");
  await signal.getByLabel("Adverse condition started").fill("2026-02-01");
  await signal.getByLabel("Why the assessment remains justified, or what changed").fill("Scope checked");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => saved?.answer.guide_ids).toEqual(["c-iam-02"]);
  expect(saved?.answer.signals[0]).toMatchObject({ source_ref: "INC-EXAMPLE-2", explanation: "Scope checked", ended_on: null });
  expect(saved?.answer.design).toEqual(RESPONSE.answer.design);
});

test("reviewer must explain approval of a response with contradictory signals", async ({ page }) => {
  const response = { ...RESPONSE, status: "submitted", contradiction_indices: [0], answer: { ...RESPONSE.answer, signals: [{
    kind: "major_incident", source_ref: "INC-EXAMPLE-3", description: "Outage", started_on: "2026-02-01", ended_on: null, explanation: "Scope checked",
  }] } };
  let request: Record<string, unknown> | undefined;
  const routes = itemRoutes((route) => route.fulfill({ json: response }));
  routes["/api/rcsa/items/7"] = (route) => route.fulfill({ json: { ...ITEM, status: "submitted" } });
  routes["/api/rcsa/items/7/responses"] = (route) => route.fulfill({ json: [response] });
  routes["/api/rcsa/responses/70/review"] = (route) => {
    request = route.request().postDataJSON();
    return route.fulfill({ json: response });
  };
  await mockApi(page, LEAD, routes);
  await page.goto("/rcsa/items/7");
  const approve = page.getByRole("button", { name: "Approve revision 1" });
  await expect(approve).toBeDisabled();
  await page.getByLabel("Reason", { exact: true }).fill("Verified scope and compensating evidence");
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect.poll(() => request?.reason).toBe("Verified scope and compensating evidence");
});
