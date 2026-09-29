import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const main = await readFile(
  new URL("../web/src/main.tsx", import.meta.url),
  "utf8",
);
const evidence = await readFile(
  new URL("../web/src/AlertEvidence.tsx", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../web/src/styles.css", import.meta.url),
  "utf8",
);

test("overview findings and attention list open the shared deviation review", () => {
  assert.match(main, /camera-finding-link/);
  assert.match(main, /alertForFinding/);
  assert.match(main, /setSelectedAlert\(relatedAlert\)/);
  assert.match(main, /function AlertReviewModal/);
  assert.match(main, /Отсутствующая техника/);
  assert.match(main, /Обнаружена техника сверх плана/);
  assert.match(main, /Сверх плана \$\{excess\} ед\. техники/);
  assert.match(main, /Простаивание техники/);
  assert.match(styles, /\.camera-finding-link/);
});

test("deviation evidence supports frames, labels, zoom and fullscreen", () => {
  assert.match(evidence, /Показывать рамки/);
  assert.match(evidence, /Показывать метки классов/);
  assert.match(evidence, /requestFullscreen/);
  assert.match(evidence, /Открыть кадр на весь экран/);
  assert.match(evidence, /alert-frame-fullscreen/);
  assert.match(evidence, /bindFrameWheel\(viewport, zoom, changeZoom\)/);
  assert.match(evidence, /event\.target === frameViewport\.current/);
  assert.match(evidence, /document\.exitFullscreen\(\)/);
  assert.ok(
    evidence.indexOf('className="icon-button alert-frame-fullscreen"') >
      evidence.indexOf('className="lab-frame-viewport"'),
  );
  assert.match(evidence, /min="100"/);
  assert.match(evidence, /max="400"/);
  assert.match(evidence, /FrameDetections/);
  assert.doesNotMatch(evidence, /bbox и классы/);
});

test("deviation evidence groups detections and filters classes", () => {
  assert.match(evidence, /Обнаружено в кадре:/);
  assert.match(evidence, /detectionGroups/);
  assert.match(evidence, /hiddenClassIds/);
  assert.match(evidence, /setClassVisible/);
  assert.match(evidence, /Показывать класс/);
});

test("deviation review shows class-filtered absence keyframes and share", () => {
  assert.match(evidence, /Ключевые кадры подтверждённого отсутствия/);
  assert.match(evidence, /evidenceClassId/);
  assert.match(evidence, /class_id=/);
  assert.match(evidence, /absence_stats/);
  assert.match(evidence, /absence_ratio/);
  assert.match(evidence, /exactFrameTime/);
  assert.match(evidence, /keyframeIndex/);
  assert.match(evidence, /aria-modal="true"/);
  assert.match(evidence, /Скрыть подробности/);
});

test("single-class evidence has no selector and batch review targets pending filtered alerts", () => {
  assert.match(
    evidence,
    /data\.deviation_class_ids\.length > 1 && \(\s*<label>/,
  );
  assert.match(main, /const pendingFiltered = filtered\.filter/);
  assert.match(main, /Принять все/);
  assert.match(main, /Отклонить все/);
  assert.match(main, /await post\(`\/alerts\/\$\{alert\.id\}\/reviews`/);
});
