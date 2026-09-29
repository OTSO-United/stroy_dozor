import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const detector = await readFile(
  new URL("../web/src/DetectorLab.tsx", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../web/src/detector.css", import.meta.url),
  "utf8",
);

test("detector exposes independent frame and class-label controls", () => {
  assert.match(detector, /Показывать рамки/);
  assert.match(detector, /Показывать метки классов/);
  assert.doesNotMatch(detector, /Показывать bbox/);
  assert.match(detector, /showFrames/);
  assert.match(detector, /showLabels/);
});

test("detector zoom is bounded and uses a scrollable viewport", () => {
  assert.match(detector, /min="100"/);
  assert.match(detector, /max="400"/);
  assert.match(detector, /Сбросить масштаб/);
  assert.match(styles, /\.lab-frame-viewport[\s\S]*?overflow: auto;/);
});

test("detector fullscreen supports backdrop close and shared wheel controls", () => {
  assert.match(detector, /requestFullscreen/);
  assert.match(detector, /Выйти из полноэкранного режима/);
  assert.match(detector, /Открыть изображение на весь экран/);
  assert.match(detector, /bindFrameWheel\(viewport, zoom, changeZoom\)/);
  assert.match(detector, /event\.target === frameViewport\.current/);
  assert.match(detector, /document\.exitFullscreen\(\)/);
  assert.match(styles, /\.lab-frame-viewer:fullscreen/);
});

test("class labels are positioned outside their detection frame", () => {
  assert.match(
    styles,
    /\.lab-detection-label\.above[\s\S]*?bottom: calc\(100% \+ 5px\)/,
  );
  assert.match(
    styles,
    /\.lab-detection-label\.below[\s\S]*?top: calc\(100% \+ 5px\)/,
  );
  assert.match(
    styles,
    /\.lab-detection-label\.right[\s\S]*?left: calc\(100% \+ 5px\)/,
  );
  assert.match(
    styles,
    /\.lab-detection-label\.left[\s\S]*?right: calc\(100% \+ 5px\)/,
  );
});

test("detector accepts a frame batch as one run and exposes frame previews", () => {
  assert.match(detector, /multiple/);
  assert.match(
    detector,
    /files\.forEach\(\(item\) => data\.append\("file", item\)\)/,
  );
  assert.match(detector, /isBatch \? "batch"/);
  assert.match(detector, /до 100 кадров как\s+одну пачку/);
  assert.match(detector, /<InfoButton title="Новый запуск детектора">/);
  assert.match(detector, /className="lab-frame-picker"/);
  assert.match(styles, /\.lab-frame-picker[\s\S]*?grid-template-columns/);
});

test("history uses preview tiles and keeps a batch in one card", () => {
  assert.match(detector, /className="lab-history-grid"/);
  assert.match(detector, /item\.preview_url/);
  assert.match(detector, /Группа ·/);
  assert.match(detector, /full: "Видео"/);
  assert.doesNotMatch(detector, /Всё видео/);
  assert.match(detector, /lab-history-kind/);
  assert.match(styles, /\.lab-history-kind\.video/);
  assert.match(styles, /\.lab-history-kind\.image/);
  assert.match(styles, /\.lab-history-kind\.batch/);
  assert.match(styles, /\.lab-history-grid[\s\S]*?repeat\(auto-fill/);
});

test("advanced model settings are collapsed and product changes require confirmation", () => {
  assert.match(detector, /<details className="lab-advanced">/);
  assert.match(detector, /Порог уверенности \(confidence\)/);
  assert.match(detector, /Перекрытие NMS \(IoU\)/);
  assert.match(detector, /Установить параметры по умолчанию/);
  assert.match(detector, /Применить для проверки детектора/);
  assert.match(detector, /Применить для фоновой обработки/);
  assert.match(detector, /disabled=\{!pageChanged \|\| savingSettings\}/);
  assert.match(detector, /disabled=\{!productChanged \|\| savingSettings\}/);
  assert.match(detector, /String\(pageParameters\.confidence\)/);
  assert.doesNotMatch(
    detector,
    /Кнопка изменения продуктового режима доступна только/,
  );
  assert.match(detector, /Фоновый мониторинг объектов/);
  assert.match(detector, />\s*Применить\s*</);
  assert.match(detector, />\s*Нет\s*</);
  assert.match(detector, />\s*Отмена\s*</);
  assert.match(
    styles,
    /\.lab-parameter-grid label[\s\S]*?flex-direction: column/,
  );
  assert.match(
    styles,
    /\.lab-parameter-grid label input[\s\S]*?margin-top: auto/,
  );
  assert.match(styles, /\.lab-submit[\s\S]*?width: 100%/);
});

test("detector page exposes tiled inference without adding it to product settings", () => {
  assert.match(detector, /Обрабатывать кадр по тайлам/);
  assert.match(detector, /Перекрытие тайлов/);
  assert.match(detector, /Без сжатия всего кадра/);
  assert.match(detector, /data\.set\("tiled", String\(pageTiling\.enabled\)\)/);
  assert.match(
    detector,
    /data\.set\("tile_overlap", String\(pageTiling\.overlap\)\)/,
  );
  assert.match(detector, /setPageTiling\(\{ \.\.\.tiling \}\)/);
  assert.match(detector, /Режим[\s\S]*?только на этой странице/);
  assert.doesNotMatch(detector, /expected_revision:[\s\S]{0,120}tiling/);
  assert.match(styles, /\.lab-tiling[\s\S]*?grid-template-columns/);
});

test("detections are grouped by class and each class controls its overlay", () => {
  assert.match(detector, /new Map<string, Detection\[\]>/);
  assert.match(detector, /className="lab-detection-groups"/);
  assert.match(detector, /checked=\{!hiddenClassIds\.has\(classId\)\}/);
  assert.match(detector, /!hiddenClassIds\.has\(d\.class_id\)/);
  assert.match(detector, /detections\.length/);
  assert.match(styles, /\.lab-detection-groups li/);
});
