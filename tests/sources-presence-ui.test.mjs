import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import esbuild from "../web/node_modules/esbuild/lib/main.js";

const main = await readFile(
  new URL("../web/src/main.tsx", import.meta.url),
  "utf8",
);
const charts = await readFile(
  new URL("../web/src/PresenceCharts.tsx", import.meta.url),
  "utf8",
);
const timeline = await readFile(
  new URL("../web/src/EquipmentTimeline.tsx", import.meta.url),
  "utf8",
);
const smoothing = await readFile(
  new URL("../web/src/chartSmoothing.ts", import.meta.url),
  "utf8",
);
const compiledSmoothing = await esbuild.transform(smoothing, {
  loader: "ts",
  format: "esm",
});
const { SMOOTHING_STEPS, defaultSmoothingStep } = await import(
  `data:text/javascript;base64,${Buffer.from(compiledSmoothing.code).toString("base64")}`
);

test("stage binding defaults to the full frame while polygons remain optional", () => {
  assert.match(main, /const fullFramePolygon:[\s\S]*?\[0, 0\][\s\S]*?\[1, 1\]/);
  assert.match(main, /aria-label={`Привязать этап/);
  assert.match(main, /e\.target\.checked \? bindFullFrame\(work\)/);
  assert.match(main, /isFullFramePolygon\(item\.polygon\)/);
  assert.match(main, /setReplacedDefault\(defaultRegion \|\| null\)/);
  assert.match(main, /Добавить полигон/);
});

test("sources expose explicit edit and confirmed delete actions", () => {
  assert.match(main, /Изменить источник/);
  assert.match(main, /Удалить источник/);
  assert.match(main, /method: "PATCH"/);
  assert.match(main, /method: "DELETE"/);
  assert.match(main, /Это действие нельзя отменить/);
});

test("overview shows only active bound stages and opens the frame viewer", () => {
  const overview = main.slice(
    main.indexOf('className="camera-source-sections"'),
    main.indexOf('id="overview-stage-summary"'),
  );
  assert.match(overview, /sourceGroups\.map\(\(group\) => \(/);
  assert.match(overview, /camera-source-section-title/);
  assert.doesNotMatch(overview, /Действующие этапы · \{liveWorks\.length\}/);
  assert.match(overview, /liveWorks\.map\(stageItem\)/);
  assert.match(overview, /completedWorks\.map\(stageItem\)/);
  assert.match(overview, /liveWorkIds\.has\(result\.work_id\)/);
  assert.match(overview, /<header\s+key=\{work\.id\}/);
  assert.match(overview, /camera-stage-title">\s*\{work\.title\}/);
  assert.match(overview, /onClick=\{\(event\) => \{/);
  assert.match(
    overview,
    /selectStageStatistics\(card\.source\.id, cardWork\.id\)/,
  );
  assert.match(overview, /setFrameCard\(card\)/);
  assert.match(overview, /<CameraFrameViewer/);
});

test("source editor awaits save and displays its error in the modal", () => {
  const editor = main.slice(
    main.indexOf("function SourceEditorModal("),
    main.indexOf("const polygonColors"),
  );
  assert.match(editor, /await onSave\(/);
  assert.match(editor, /setSaveError\(\(error as Error\)\.message\)/);
  assert.match(editor, /role="alert"/);
  assert.match(main, /setEditSource\(null\)/);
});

test("presence charts only list classes required by the current plan", () => {
  assert.match(charts, /plannedClassIds/);
  assert.match(charts, /\(plan\?\.works \|\| \[\]\)/);
  assert.match(charts, /count > 0/);
  assert.match(charts, /\.filter\(\(c\) => plannedClassIds\.has\(c\.id\)\)/);
  assert.match(charts, /Каждая строка относится к одному виду техники/);
  assert.match(charts, /<InfoButton title="Динамика присутствия">/);
});

test("charts distinguish loading, show navigation below the plot and explain activity by class", () => {
  assert.match(charts, /Загружаем историю/);
  assert.match(charts, /В выбранном периоде нет измерений/);
  assert.ok(
    charts.indexOf('className="chart-history-controls"') >
      charts.indexOf('mode="presence"'),
  );
  assert.match(charts, /display_activity_by_class/);
  assert.match(charts, /<ActivitySummary/);
  assert.match(charts, /<InfoButton title="Активность техники">/);
});

test("chart hover time includes seconds and sampled levels connect only within a continuous segment", () => {
  assert.match(timeline, /second: "2-digit"/);
  assert.match(
    timeline,
    /points\[left\]\.segment === points\[right\]\.segment/,
  );
  assert.match(timeline, /const linked = sameSeries/);
  assert.match(timeline, /onPointClick\?: \(point: Point\)/);
  assert.match(charts, /CameraFrameViewer/);
  assert.match(charts, /\/observations\/\$\{framePoint\.id\}/);
  assert.match(charts, /appliedPreference\.current === preferenceKey/);
  assert.match(timeline, /equipment-timeline-dot/);
});

test("file source form warns about the bounded demo loop without persistent badges", () => {
  assert.match(main, /Циклически повторять видео/);
  assert.match(main, /Тестовый демо-режим/);
  assert.match(main, /demo_loop_enabled/);
  assert.match(main, /demo_loop_duration_seconds/);
  assert.match(main, /автоматически остановится в момент его окончания/);
  assert.match(main, /className="demo-duration-fields"/);
  assert.match(main, /demoDurationSeconds/);
  assert.match(main, /max="59"/);
  assert.doesNotMatch(main, /Длительность демонстрации, часов/);
  assert.doesNotMatch(main, /max="8784"/);
  assert.doesNotMatch(main, /ТЕСТ · ЦИКЛ/);
});

test("completed analysis has a result status and a direct statistics action", () => {
  assert.match(main, /completed: "Результаты готовы"/);
  assert.match(main, /ready: "Отклонений не выявлено"/);
  assert.doesNotMatch(main, /Сопоставление выполнено/);
  assert.match(main, /Открыть результаты и статистику/);
  assert.match(main, /Результаты и статистика/);
  assert.match(main, /preferredStatisticsSourceId/);
  assert.match(charts, /preferredSourceId/);
  assert.match(charts, /id="project-statistics"/);
});

test("zone editor closes only after a confirmed save and exposes save errors", () => {
  assert.match(main, /onSave: \(b:[\s\S]*?\) => Promise<void>/);
  assert.match(main, /await onSave\(\{/);
  assert.match(main, /onClose\(\);/);
  assert.match(main, /setSaveError\(\(error as Error\)\.message\)/);
  assert.match(main, /saving \? "Сохраняем…" : "Сохранить привязки"/);
});

test("source recording time uses stable date, hour and minute controls", () => {
  assert.match(main, /function StableDateTimeInput/);
  assert.match(main, /aria-label="Часы начала записи"/);
  assert.match(main, /aria-label="Минуты начала записи"/);
  assert.match(main, /Array\.from\(\{ length: 60 \}/);
  assert.match(main, /<StableDateTimeInput[\s\S]*?value=\{start\}/);
});

test("charts expose larger display steps, a custom duration and history paging", () => {
  assert.match(smoothing, /\[86400, "1 сутки"\]/);
  assert.match(charts, /Длительность наблюдений/);
  assert.match(charts, /Другое значение…/);
  assert.match(charts, /Произвольная длительность наблюдений в часах/);
  assert.match(charts, /step="any"/);
  assert.match(charts, /next_before/);
  assert.match(charts, /← Ранее/);
  assert.match(charts, /Новее →/);
  assert.match(charts, /windowStart/);
});

test("presence charts expose smoothed summary and confirmed absence share", () => {
  assert.match(main, /Подтверждение отсутствия машины, секунд/);
  assert.match(main, /absence_confirm_seconds/);
  assert.match(charts, /Сводка по времени/);
  assert.match(charts, /Подробная динамика/);
  assert.match(charts, /Не обнаружено \/ подтверждённо отсутствует/);
  assert.match(charts, /absenceRatio/);
  assert.match(charts, /point\.presence/);
});

test("presence charts select one bound plan stage instead of mixing stages", () => {
  assert.match(charts, /Этап плана/);
  assert.match(charts, /aria-label="Этап для графика"/);
  assert.match(charts, /sourceCard\?\.binding\?\.regions/);
  assert.match(charts, /work_id=\$\{selectedWork\.id\}/);
  assert.match(charts, /selectedWork\?\.resources/);
});

test("additional chart includes unplanned detected classes and live history keeps earlier runs", () => {
  assert.match(charts, /Присутствие дополнительной техники/);
  assert.match(charts, /!plannedClassIds\.has\(item\.id\)/);
  assert.match(charts, /mode="additional"/);
  assert.doesNotMatch(charts, /Присутствие и активность техники/);
  assert.doesNotMatch(
    charts,
    /filter\(\(p\) => p\.run_id === series\?\.points\.at\(-1\)/,
  );
});

test("default smoothing rounds six source samples up to existing options", () => {
  assert.deepEqual(
    SMOOTHING_STEPS.map(([seconds]) => seconds),
    [30, 60, 180, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400],
  );
  assert.equal(defaultSmoothingStep(1), 30);
  assert.equal(defaultSmoothingStep(10), 60);
  assert.equal(defaultSmoothingStep(30), 180);
  assert.equal(defaultSmoothingStep(31), 300);
  assert.equal(defaultSmoothingStep(3600), 28800);
  assert.match(charts, /defaultSmoothingStep\(source\?\.sample_seconds\)/);
  assert.match(charts, /stepOverride\?\.context === smoothingContext/);
  assert.match(charts, /selectedWork\?\.id/);
});
