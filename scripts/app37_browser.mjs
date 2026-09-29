// Run against the isolated database produced by test_idle_regression.py.
import { chromium } from "../tmp/app29-tools/node_modules/playwright/index.mjs";
import fs from "node:fs/promises";
import assert from "node:assert/strict";

const base = process.env.APP37_URL || "http://127.0.0.1:8097";
assert.equal(new URL(base).hostname, "127.0.0.1");
const out = "tmp/app37-browser";
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const report = [];
let lastPage;
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
    });
    const page = await context.newPage();
    lastPage = page;
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const [project] = await (
      await context.request.get(`${base}/api/v1/projects`)
    ).json();
    const [source] = await (
      await context.request.get(`${base}/api/v1/projects/${project.id}/sources`)
    ).json();
    const secondId = "app37-other-object";
    let jobs = [];
    let polls = 0;
    await page.route("**/api/v1/projects", (route) =>
      route.fulfill({
        json: [
          { ...project, name: "Объект A" },
          { ...project, id: secondId, name: "Объект B" },
        ],
      }),
    );
    await page.route(`**/api/v1/projects/${secondId}/**`, async (route) => {
      const response = await route.fetch({
        url: route.request().url().replace(secondId, project.id),
      });
      await route.fulfill({ response });
    });
    await page.route("**/api/v1/projects/*/jobs", (route) => {
      polls++;
      return route.fulfill({ json: jobs });
    });
    await page.clock.install();
    await page.goto(`${base}/?project=${project.id}&tab=overview`);
    await page
      .locator('[aria-label="Вид активности техники"]')
      .getByRole("button", { name: "Сводка по времени" })
      .click();
    await page
      .locator(".activity-summary .absence-histogram-bar .absent")
      .waitFor();
    const summary = await page.locator(".activity-summary").innerText();
    assert.match(summary, /простой 55/);
    assert.equal(await page.locator(".toast").count(), 0);
    await page.screenshot({
      path: `${out}/activity-${width}.png`,
      fullPage: true,
    });
    const makeJob = async (id, status = "succeeded") => ({
      id,
      source_id: source.id,
      kind: "analyze",
      status,
      started_at: await page.evaluate(() => new Date().toISOString()),
      updated_at: await page.evaluate(() => new Date().toISOString()),
      results_ready: status === "succeeded",
      works: [
        {
          id: `stage-${id}`,
          code: "12.2",
          title: "Очень длинное название этапа ".repeat(15),
        },
      ],
      error: status === "failed" ? "Источник недоступен" : null,
    });
    async function refresh() {
      const before = polls;
      const response = page.waitForResponse((r) => r.url().endsWith("/jobs"));
      await page.clock.runFor(5100);
      await response;
      assert(polls > before);
    }
    for (const count of [1, 3, 20]) {
      jobs = await Promise.all(
        Array.from({ length: count }, (_, i) => makeJob(`${count}-${i}`)),
      );
      await refresh();
      await page
        .locator(".toast strong")
        .filter({ hasText: `Завершён анализ ${count} ` })
        .waitFor();
      const before = await page.locator(".toast-copy strong").allTextContents();
      await refresh();
      assert.deepEqual(
        await page.locator(".toast-copy strong").allTextContents(),
        before,
      );
      const geometry = await page.locator(".toast").evaluateAll((nodes) =>
        nodes.map((node) => {
          const r = node.getBoundingClientRect();
          return {
            height: r.height,
            left: r.left,
            right: r.right,
            bottom: r.bottom,
          };
        }),
      );
      assert(geometry.length <= 3);
      assert(
        geometry.every(
          (r) =>
            r.height <= 104 &&
            r.left >= 0 &&
            r.right <= width &&
            r.bottom <= 900,
        ),
      );
      if (count === 20)
        await page.screenshot({ path: `${out}/notices-${width}.png` });
      await page.clock.fastForward(7100);
      await page.waitForFunction(
        () => document.querySelectorAll(".toast").length === 0,
      );
    }
    jobs = [
      ...jobs,
      await makeJob("failure", "failed"),
      await makeJob("cancelled", "cancelled"),
      await makeJob("new-success"),
    ];
    await refresh();
    await page.locator(".toast-error").waitFor();
    assert((await page.locator(".toast").count()) <= 3);
    await page.screenshot({ path: `${out}/mixed-${width}.png` });
    await page
      .locator(".toast-error")
      .getByRole("button", { name: "Закрыть уведомление" })
      .click();
    assert.equal(await page.locator(".toast-error").count(), 0);
    await page.clock.fastForward(20000);
    await page.waitForFunction(
      () => document.querySelectorAll(".toast").length === 0,
    );

    jobs = [await makeJob("site-a-ready")];
    await refresh();
    await page
      .locator(".toast-copy > span")
      .filter({ hasText: "Объект A" })
      .first()
      .waitFor();
    await page.getByRole("link", { name: "Все объекты", exact: true }).click();
    jobs = [await makeJob("site-b-ready")];
    await page
      .locator(".project-tile-open")
      .filter({ hasText: "Объект B" })
      .click();
    await page
      .locator(".toast-copy > span")
      .filter({ hasText: "2 объекта: Объект A, Объект B" })
      .first()
      .waitFor();
    assert((await page.locator(".toast").count()) <= 3);
    await page.screenshot({ path: `${out}/two-projects-${width}.png` });
    await page.reload();
    await page.locator('[aria-label="Вид активности техники"]').waitFor();
    assert.equal(await page.locator(".toast").count(), 0);
    assert.deepEqual(errors, []);
    report.push({ width, summary, errors, polls });
    await context.close();
  }
} catch (error) {
  console.log(await lastPage.locator("body").innerText());
  await lastPage.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
await fs.writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
