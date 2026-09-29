import { chromium } from "../tmp/app29-tools/node_modules/playwright/index.mjs";
import fs from "node:fs/promises";
import assert from "node:assert/strict";

const fixture = JSON.parse(
  await fs.readFile("tmp/app29-runtime/fixture.json", "utf8"),
);
const out = "tmp/app29-browser";
await fs.mkdir(out, { recursive: true });
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
try {
  assert(new URL(fixture.url).host === "127.0.0.1:8091");
  const api = `http://127.0.0.1:8091/api/v1`;
  const planData = await (
    await context.request.get(`${api}/projects/${fixture.project_id}/plans`)
  ).json();
  const unboundId = planData.plans[0].works.find(
    (work) => work.code === "12.3",
  ).id;
  const sources = await (
    await context.request.get(`${api}/projects/${fixture.project_id}/sources`)
  ).json();
  for (const source of sources) {
    const binding = await (
      await context.request.get(`${api}/sources/${source.id}/bindings`)
    ).json();
    if (binding.regions.some((region) => region.work_ids.includes(unboundId))) {
      const response = await context.request.post(
        `${api}/sources/${source.id}/bindings`,
        {
          headers: { "Idempotency-Key": crypto.randomUUID() },
          data: {
            expected_revision: binding.revision,
            regions: binding.regions
              .map((region) => ({
                ...region,
                work_ids: region.work_ids.filter((id) => id !== unboundId),
              }))
              .filter((region) => region.work_ids.length),
          },
        },
      );
      assert(response.ok());
    }
  }
  await page.goto(fixture.url);
  await page
    .getByRole("heading", { name: "Наблюдение за площадкой", exact: true })
    .waitFor();
  await page
    .getByRole("heading", { name: "Итог по этапу", exact: true })
    .waitFor();
  await page.locator(".presence-panel svg").waitFor();
  await page.screenshot({ path: `${out}/overview-wide.png`, fullPage: true });
  const plot = await page
    .locator('.presence-panel svg[role="img"]')
    .evaluate((svg) => ({
      paths: [...svg.querySelectorAll("path")].map((p) => p.getAttribute("d")),
      ticks: [...svg.querySelectorAll('text[text-anchor="end"]')].map(
        (n) => n.textContent,
      ),
    }));
  assert(
    plot.paths.length > 0 &&
      plot.paths.every((path) => /^[MH\d.,\s-]+$/.test(path)),
  );
  assert.deepEqual(plot.ticks.slice(0, 5), ["0", "1", "2", "3", "4"]);
  const summaryTone = await page
    .locator("#overview-stage-summary section")
    .getAttribute("data-tone");
  assert.equal(summaryTone, "warning");
  await page.getByRole("button", { name: "План работ", exact: true }).click();
  await page
    .getByRole("heading", { name: "План работ", exact: true })
    .waitFor();
  await page.locator(".work-row").first().waitFor();
  await page.screenshot({ path: `${out}/plan-wide.png`, fullPage: true });
  const emptyRow = page
    .locator(".work-row")
    .filter({ hasText: "Этап без привязанной камеры" });
  assert.equal(await emptyRow.locator(".work-source-add-empty").count(), 1);
  await emptyRow.getByRole("button", { name: /Добавить камеру/ }).click();
  await page.getByRole("heading", { name: "Источники этапов" }).waitFor();
  await page.screenshot({ path: `${out}/binding-wide.png`, fullPage: true });
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  assert.equal(await page.locator(".toast").count(), 0);
  await emptyRow.getByRole("button", { name: /Добавить камеру/ }).click();
  const bindingsPattern = "**/api/v1/sources/*/bindings";
  await page.route(bindingsPattern, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 503,
          json: { detail: "Ошибка сохранения для проверки" },
        })
      : route.continue(),
  );
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await page
    .locator(".modal")
    .getByRole("alert")
    .filter({ hasText: "Ошибка сохранения" })
    .waitFor();
  assert.equal(await page.locator(".toast").count(), 0);
  await page.unroute(bindingsPattern);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(bindingsPattern, async (route) => {
    if (route.request().method() === "POST") await gate;
    await route.continue();
  });
  const pending = page.waitForRequest(
    (request) =>
      request.url().includes("/bindings") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await pending;
  assert.equal(await page.locator(".toast").count(), 0);
  assert(
    await page
      .getByRole("button", { name: "Отмена", exact: true })
      .isDisabled(),
  );
  await page.keyboard.press("Escape");
  assert.equal(
    await page.getByRole("heading", { name: "Источники этапов" }).count(),
    1,
  );
  release();
  await page.getByRole("heading", { name: /^Редактировать зоны/ }).waitFor();
  await page.unroute(bindingsPattern);
  await page.getByText(/Источник привязан ·/).waitFor();
  await page.waitForFunction(
    () =>
      document.querySelector(
        'input[aria-label="Привязать этап 12.3 к источнику"]',
      )?.checked,
  );
  await page
    .getByRole("checkbox", { name: "Привязать этап 12.3 к источнику" })
    .uncheck();
  await page
    .getByRole("button", { name: "Сохранить привязки", exact: true })
    .click();
  await page.locator(".modal").waitFor({ state: "hidden" });
  await emptyRow.locator(".work-source-add-empty").waitFor();
  await page
    .getByRole("button", { name: "Редактировать план", exact: true })
    .click();
  await page.getByRole("heading", { name: "Редактор плана" }).waitFor();
  await page.screenshot({ path: `${out}/editor-wide.png`, fullPage: true });
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${out}/plan-narrow.png`, fullPage: true });
  const dimensions = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  assert(dimensions.scroll <= dimensions.width + 1, JSON.stringify(dimensions));
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page
    .locator(".page-actions")
    .getByRole("button", { name: "Обзор объекта", exact: true })
    .click();
  assert(
    new URL(page.url()).searchParams.get("project") === fixture.project_id,
  );
  await page.locator(".attention-content .alert-list button").first().click();
  await page
    .getByRole("heading", { name: "Проверка отклонения", exact: true })
    .waitFor();
  await page.locator(".absence-keyframes button").first().waitFor();
  await page.screenshot({ path: `${out}/evidence-wide.png`, fullPage: true });
  const equipmentOptions = await page
    .getByLabel("Вид техники", { exact: true })
    .locator("option")
    .allTextContents();
  assert.deepEqual(equipmentOptions, ["Все отсутствующие классы", "Самосвал"]);
  await page.getByLabel("Вид техники", { exact: true }).selectOption("0");
  await page.locator(".absence-keyframes button").first().waitFor();
  await page.locator(".alert-frame-viewer .lab-frame img").click();
  await page.waitForFunction(() => Boolean(document.fullscreenElement));
  await page
    .getByRole("button", { name: "Увеличить кадр", exact: true })
    .click();
  assert.equal(
    await page.locator(".lab-zoom-controls output").innerText(),
    "125%",
  );
  await page
    .getByRole("checkbox", { name: "Показывать рамки", exact: true })
    .uncheck();
  assert.equal(await page.locator(".lab-detection.show-frame").count(), 0);
  await page
    .getByRole("checkbox", { name: "Показывать рамки", exact: true })
    .check();
  await page.screenshot({ path: `${out}/fullscreen-wide.png` });
  await page
    .getByRole("button", {
      name: "Выйти из полноэкранного просмотра кадра",
      exact: true,
    })
    .click();
  await page.waitForFunction(() => !document.fullscreenElement);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${out}/evidence-narrow.png`, fullPage: true });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  await page.setViewportSize({ width: 1440, height: 1050 });
  const reviewsPattern = "**/api/v1/alerts/*/reviews";
  await page.route(reviewsPattern, (route) =>
    route.fulfill({ status: 503, json: { detail: "Проверка ошибки решения" } }),
  );
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await page
    .locator(".modal")
    .getByRole("alert")
    .filter({ hasText: "Проверка ошибки решения" })
    .waitFor();
  await page.unroute(reviewsPattern);
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await page.locator(".modal").waitFor({ state: "hidden" });
  await page.getByRole("tab", { name: /Проверено · 1/ }).waitFor();
  assert.equal(
    await page
      .locator("#overview-stage-summary section")
      .getAttribute("data-tone"),
    "warning",
  );
  await page.reload();
  await page.getByRole("tab", { name: /Проверено · 1/ }).waitFor();
  assert.equal(
    await page.locator(".attention-content .alert-list button").count(),
    3,
  );
  await page.locator(".attention-content .alert-list button").first().click();
  await page
    .getByRole("button", { name: "Ложный сигнал", exact: true })
    .click();
  await page.locator(".modal").waitFor({ state: "hidden" });
  await page.getByRole("tab", { name: /Проверено · 2/ }).click();
  await page.getByText(/Ложный сигнал ·/).waitFor();
  await page.getByText(/Подтверждён оператором ·/).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${out}/overview-narrow.png`, fullPage: true });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole("button", { name: "Отчёты", exact: true }).click();
  await page
    .getByRole("heading", { name: "Отчёты по объекту", exact: true })
    .waitFor();
  await page.screenshot({ path: `${out}/reports-wide.png`, fullPage: true });
  const reportHref = await page
    .getByRole("link", { name: "Скачать Excel", exact: true })
    .getAttribute("href");
  const reportUrl = new URL(reportHref, fixture.url);
  const reportEnd = Date.parse(reportUrl.searchParams.get("to"));
  assert(
    Math.abs(Date.now() - reportEnd) < 65000,
    "Report end must represent the current instant even in a New York browser",
  );
  const response = await context.request.get(reportUrl.href);
  assert.equal(response.status(), 200);
  await fs.writeFile(`${out}/report.xlsx`, await response.body());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${out}/reports-narrow.png`, fullPage: true });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page
    .getByRole("button", { name: "Камеры и видео", exact: true })
    .click();
  await page
    .locator(".page-actions")
    .getByRole("button", { name: "Добавить источник", exact: true })
    .click();
  await page.getByRole("button", { name: "RTSP / HTTPS", exact: true }).click();
  await page
    .getByLabel("Название", { exact: true })
    .fill("APP-29 HTTPS form fixture");
  await page
    .getByLabel("Адрес RTSP, RTSPS или HTTPS", { exact: true })
    .fill("https://127.0.0.1:18443/local-test.mp4");
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/sources"),
  );
  await page
    .locator(".modal")
    .getByRole("button", { name: "Добавить источник", exact: true })
    .click();
  const savedSource = (await (await saved).json()).source;
  assert.equal(savedSource.kind, "rtsp");
  await page
    .getByText("Источник добавлен. Привяжите его к этапу для анализа.", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await page.locator(".toast").filter({ hasText: "Начат анализ" }).count(),
    0,
  );
  const pendingSources = await (
    await context.request.get(`${api}/projects/${fixture.project_id}/sources`)
  ).json();
  assert.equal(
    pendingSources.find((source) => source.id === savedSource.id)
      .processing_state,
    "preparing",
  );
  assert(
    (
      await context.request.post(`${api}/sources/${savedSource.id}/stop`, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).ok(),
  );
  assert(
    (
      await context.request.post(`${api}/sources/${savedSource.id}/bindings`, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
        data: {
          expected_revision: 0,
          regions: [
            {
              name: "Whole frame",
              work_ids: [unboundId],
              polygon: [
                [0, 0],
                [1, 0],
                [1, 1],
                [0, 1],
              ],
              visibility_confirmed: true,
            },
          ],
        },
      })
    ).ok(),
  );
  await page.goto(fixture.url.replace("tab=overview", "tab=plan"));
  const linkedRow = page
    .locator(".work-row")
    .filter({ hasText: "Этап без привязанной камеры" });
  await linkedRow
    .locator(".work-source-tile")
    .filter({ hasText: savedSource.name })
    .waitFor();
  assert.equal(await linkedRow.locator(".work-source-add-empty").count(), 0);
  assert(
    (
      await context.request.delete(`${api}/sources/${savedSource.id}`, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).ok(),
  );
  await page.reload();
  await linkedRow.locator(".work-source-add-empty").waitFor();
  assert.deepEqual(errors, []);
  await fs.writeFile(
    `${out}/checks.json`,
    JSON.stringify(
      {
        viewportWidths: [1440, 390],
        timezone: "America/New_York",
        binding: "cancel/error/pending/success/unbind/delete-last-source",
        reviews: ["confirmed", "dismissed"],
        fullscreen: "open/zoom/frames/close",
        charts: "horizontal paths, integer ticks, real gaps",
        httpsForm:
          "saved to API, preparation pending, no false analysis notice; decoding is tested separately",
        reportUrl: reportUrl.href,
        errors,
      },
      null,
      2,
    ),
  );
  console.log("APP-29 browser checks passed", reportUrl.href);
} catch (error) {
  await page.screenshot({ path: `${out}/failure.png` });
  console.error(
    await page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll("body *")]
        .map((el) => ({
          tag: el.tagName,
          className: el.className,
          x: el.getBoundingClientRect().x,
          width: el.getBoundingClientRect().width,
        }))
        .filter(
          (el) => el.width && (el.x < -1 || el.x + el.width > innerWidth + 1),
        )
        .slice(0, 30),
    })),
  );
  console.error(error);
  throw error;
} finally {
  await browser.close();
}
