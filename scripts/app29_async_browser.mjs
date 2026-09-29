import { chromium } from "../tmp/app29-tools/node_modules/playwright/index.mjs";
import fs from "node:fs/promises";
import assert from "node:assert/strict";

const fixture = JSON.parse(
  await fs.readFile("tmp/app29-runtime/fixture.json", "utf8"),
);
const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1050 },
  timezoneId: "America/New_York",
});
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let jobs = [
  {
    id: "app29-delayed",
    kind: "analyze",
    status: "queued",
    works: [
      { id: "one", code: "12.1", title: "Первый этап запуска" },
      { id: "two", code: "12.2", title: "Второй этап запуска" },
    ],
    started_at: null,
    results_ready: false,
    continuous: false,
  },
];
let polls = 0;
await page.route("**/api/v1/projects/*/jobs", (route) => {
  polls++;
  return route.fulfill({ json: jobs });
});
await page.clock.install();
async function poll() {
  const before = polls;
  await page.clock.runFor(5100);
  await page.waitForFunction(() => true);
  assert(polls > before);
}
try {
  await page.goto(fixture.url);
  await page.locator(".presence-panel svg").waitFor();
  assert.equal(await page.locator(".toast").count(), 0);
  const started = await page.evaluate(() => new Date().toISOString());
  jobs = [
    { ...jobs[0], status: "running", started_at: started },
    {
      ...jobs[0],
      id: "app29-continuous",
      status: "running",
      started_at: started,
      continuous: true,
      works: [{ id: "parallel", code: "99.7", title: "Параллельный поток" }],
    },
  ];
  await poll();
  await page
    .locator(".toast")
    .filter({
      hasText: "Начат анализ 3 этапов",
    })
    .waitFor();
  jobs[0] = { ...jobs[0], status: "succeeded", analysis_state: "assessing" };
  await poll();
  assert.equal(
    await page.locator(".toast").filter({ hasText: "Завершён" }).count(),
    0,
  );
  jobs[0] = { ...jobs[0], results_ready: true, analysis_state: "ready" };
  await poll();
  await page
    .locator(".toast")
    .filter({
      hasText: "Завершён анализ 2 этапов",
    })
    .waitFor();
  assert.equal(
    await page
      .locator(".toast")
      .filter({ hasText: "Параллельный поток завершён" })
      .count(),
    0,
  );
  await page.reload();
  await page.locator(".presence-panel svg").waitFor();
  assert.equal(await page.locator(".toast").count(), 0);
  jobs.push({
    ...jobs[0],
    id: "app29-cancelled",
    status: "cancelled",
    started_at: null,
    results_ready: false,
  });
  await poll();
  assert.equal(await page.locator(".toast").count(), 0);
  jobs.push({
    ...jobs[0],
    id: "app29-repeat",
    status: "running",
    started_at: await page.evaluate(() => new Date().toISOString()),
    results_ready: false,
  });
  await poll();
  await page
    .locator(".toast")
    .filter({ hasText: "Начат анализ 2 этапов" })
    .waitFor();
  jobs[jobs.length - 1].status = "failed";
  await poll();
  assert.equal(
    await page.locator(".toast").filter({ hasText: "Завершён" }).count(),
    0,
  );

  await page.locator(".attention-content .alert-list button").first().click();
  await page.locator(".absence-keyframes button").first().waitFor();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let delayed;
  const received = new Promise((resolve) => {
    delayed = resolve;
  });
  await page.route("**/api/v1/alerts/*/evidence?**", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (new URL(route.request().url()).searchParams.get("class_id") === "0") {
      delayed();
      await gate;
      body.keyframes = body.keyframes.map((frame) => ({
        ...frame,
        image_url: `${frame.image_url}?obsolete=1`,
      }));
    }
    await route.fulfill({ json: body }).catch(() => {});
  });
  await page.getByLabel("Вид техники", { exact: true }).selectOption("0");
  await received;
  assert.equal(await page.locator(".absence-keyframes button").count(), 0);
  await page.getByLabel("Вид техники", { exact: true }).selectOption("all");
  await page.locator(".absence-keyframes button").first().waitFor();
  release();
  await page.clock.runFor(100);
  assert.equal(
    await page.getByLabel("Вид техники", { exact: true }).inputValue(),
    "all",
  );
  assert.equal(await page.locator('img[src*="obsolete"]').count(), 0);
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();

  let monitor;
  let gotMonitor;
  const firstMonitor = new Promise((resolve) => {
    gotMonitor = resolve;
  });
  await page.route("**/api/v1/projects/*/monitoring", async (route) => {
    const response = await route.fetch();
    monitor ??= await response.json();
    gotMonitor();
    await route.fulfill({ json: monitor });
  });
  await poll();
  await firstMonitor;
  const originalPlan = structuredClone(monitor.plan);
  const future = new Date(Date.now() + 2 * 86400000).toISOString();
  monitor.plan = {
    ...originalPlan,
    id: "future-plan",
    works: originalPlan.works.map((work) => ({ ...work, starts_at: future })),
  };
  await poll();
  await page.getByRole("button", { name: "Отчёты", exact: true }).click();
  await page
    .getByText("Конец периода должен быть позже начала", { exact: true })
    .waitFor();
  assert.equal(
    await page.getByText("Скачать Excel", { exact: true }).getAttribute("href"),
    null,
  );
  await page
    .getByRole("button", { name: "Выбрать дату начала периода", exact: true })
    .click();
  const today = await page.evaluate(() =>
    new Date().toLocaleDateString("ru", {
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: "Europe/Moscow",
    }),
  );
  await page.getByRole("button", { name: today, exact: true }).click();
  await page
    .getByLabel("Часы начала периода этапа", { exact: true })
    .selectOption("00");
  const manual = await page
    .getByRole("link", { name: "Скачать Excel", exact: true })
    .getAttribute("href");
  monitor.plan = { ...originalPlan, id: "later-response-plan" };
  await poll();
  assert.equal(
    await page
      .getByRole("link", { name: "Скачать Excel", exact: true })
      .getAttribute("href"),
    manual,
  );
  monitor.plan = null;
  await poll();
  await page
    .getByRole("button", { name: "Обзор объекта", exact: true })
    .click();
  await page.getByRole("button", { name: "Отчёты", exact: true }).click();
  await page
    .getByText("Конец периода должен быть позже начала", { exact: true })
    .waitFor();
  await page
    .getByLabel("Часы начала периода этапа", { exact: true })
    .selectOption("00");
  await page
    .getByRole("link", { name: "Скачать Excel", exact: true })
    .waitFor();
  monitor.plan = originalPlan;
  await page.route("**/api/v1/catalog", async (route) => {
    const data = await (await route.fetch()).json();
    const longest = [...data.classes].sort(
      (a, b) => b.name.length - a.name.length,
    )[0].name;
    data.classes.find((item) => item.id === 0).name = longest;
    await route.fulfill({ json: data });
  });
  await page.goto(fixture.url);
  await page.locator(".attention-content .alert-list button").first().click();
  await page.locator(".absence-keyframes button").first().waitFor();
  await page.getByLabel("Вид техники", { exact: true }).selectOption("0");
  await page.locator(".absence-keyframes button").first().waitFor();
  assert(
    (await page.getByLabel("Вид техники", { exact: true }).boundingBox())
      .width >= 400,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "tmp/app29-browser/longest-class-narrow.png" });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  const label = page.locator(".absence-stat-grid button span").first();
  assert.equal(
    await label.innerText(),
    "Лёгкий коммерческий фургон (Газель/аналог)",
  );
  assert(
    await label.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  );
  assert.deepEqual(errors, []);
  await fs.writeFile(
    "tmp/app29-browser/async-checks.json",
    JSON.stringify(
      {
        notifications:
          "queue/start/delayed-assessment/ready/parallel/continuous/reload/cancel/error/repeat",
        evidenceRace: "late class response cannot replace newer selection",
        reportDates: "future/no-plan/manual/late-plan/timezone",
        longLabel:
          "layout stress with the longest real catalog name at 1440/390; response label substitution only",
        errors,
      },
      null,
      2,
    ),
  );
  console.log("APP-29 asynchronous browser checks passed");
} catch (error) {
  await page.screenshot({ path: "tmp/app29-browser/async-failure.png" });
  throw error;
} finally {
  await browser.close();
}
