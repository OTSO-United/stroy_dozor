import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join } from "node:path";
import esbuild from "../web/node_modules/esbuild/lib/main.js";

const requireWeb = createRequire(
  new URL("../web/package.json", import.meta.url),
);
const React = requireWeb("react");
const { renderToStaticMarkup } = requireWeb("react-dom/server");
const outputDir = fileURLToPath(
  new URL("../web/node_modules/.cache/stroy-timeline-test/", import.meta.url),
);
await mkdir(outputDir, { recursive: true });
const outputFile = join(outputDir, "EquipmentTimeline.mjs");
await esbuild.build({
  entryPoints: [
    fileURLToPath(new URL("../web/src/EquipmentTimeline.tsx", import.meta.url)),
  ],
  outfile: outputFile,
  bundle: true,
  platform: "node",
  format: "esm",
  packages: "external",
  jsx: "automatic",
});
const { EquipmentTimeline } = await import(pathToFileURL(outputFile).href);

function point(index, segment = 0) {
  return {
    id: String(index),
    time: new Date(Date.UTC(2026, 8, 28, 8, index)).toISOString(),
    offset_seconds: index * 60,
    counts: { 1: 2 },
    raw_counts: { 1: 2 },
    display_basis: { 1: "confirmed" },
    run_id: "run",
    segment,
    activity: null,
    demo: false,
  };
}
const render = (points, row, mode, displayStep = 60) =>
  renderToStaticMarkup(
    React.createElement(EquipmentTimeline, {
      points,
      rows: [row],
      displayStep,
      mode,
    }),
  );
const count = (markup, phrase) => markup.split(phrase).length - 1;

test("presence draws plan and labels each unchanged level once", () => {
  const points = [point(0), point(1), point(2), point(3), point(4)];
  const markup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 2,
      values: [1, 1, 2, 3, 3],
    },
    "presence",
  );
  assert.match(markup, /equipment-timeline-planned/);
  assert.match(markup, /equipment-icons\/1\.webp/);
  assert.equal(count(markup, 'class="equipment-timeline-value below"'), 1);
  assert.equal(count(markup, 'class="equipment-timeline-value match"'), 1);
  assert.equal(count(markup, 'class="equipment-timeline-value above"'), 1);
  assert.match(markup, /План: 2 ед/);
});

test("activity stacks one working and one idle excavator without repeating labels", () => {
  const points = [point(0), point(1), point(2)];
  const markup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 2,
      values: [2, 2, 2],
      activity: [
        { working: 1, idle: 1, unknown: 0 },
        { working: 1, idle: 1, unknown: 0 },
        { working: 0, idle: 2, unknown: 0 },
      ],
    },
    "activity",
  );
  assert.match(markup, /equipment-timeline-fill working/);
  assert.match(markup, /equipment-timeline-fill idle/);
  assert.equal(count(markup, 'class="equipment-timeline-value working"'), 0);
  assert.equal(count(markup, 'class="equipment-timeline-value idle"'), 0);
  assert.match(markup, /class="equipment-timeline-y-label"[^>]*>1<\/text>/);
  assert.match(markup, /class="equipment-timeline-y-label"[^>]*>2<\/text>/);
});

test("unclassified activity is gray and a new segment restarts its number", () => {
  const points = [point(0), point(1), point(2, 1)];
  const activityMarkup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 2,
      values: [2, 2, 2],
      activity: [
        { working: 0, idle: 0, unknown: 2 },
        { working: 0, idle: 0, unknown: 2 },
        { working: 0, idle: 0, unknown: 2 },
      ],
    },
    "activity",
  );
  assert.match(activityMarkup, /equipment-timeline-fill unknown/);
  assert.doesNotMatch(activityMarkup, /equipment-timeline-fill working/);
  assert.doesNotMatch(activityMarkup, /equipment-timeline-fill idle/);
  const presenceMarkup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 2,
      values: [2, 2, 2],
    },
    "presence",
  );
  assert.equal(
    count(presenceMarkup, 'class="equipment-timeline-value match"'),
    2,
  );
});

test("sampled levels connect and keep smaller dots without repeating an unchanged number", () => {
  const points = [point(0), point(1), point(2)];
  for (const entry of points) entry.display_basis["1"] = "sampled";
  const markup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 2,
      values: [1, 1, 1],
    },
    "presence",
  );
  assert.equal(count(markup, 'class="equipment-timeline-value below"'), 1);
  assert.equal(count(markup, 'class="equipment-timeline-dot below"'), 3);
  assert.match(markup, /equipment-timeline-fill below/);
  assert.match(markup, /r="2"/);
});

test("dense presence samples leave enough horizontal space between value labels", () => {
  const points = Array.from({ length: 120 }, (_, index) => point(index));
  const values = points.map((_, index) => (Math.floor(index / 6) % 2 ? 1 : 2));
  const markup = render(
    points,
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 1,
      values,
    },
    "presence",
  );
  const positions = [
    ...markup.matchAll(/class="equipment-timeline-value [^"]+" x="([\d.]+)"/g),
  ].map((match) => Number(match[1]));
  assert.ok(positions.length > 0);
  assert.ok(positions.length < values.length / 2);
  assert.ok(
    positions.every(
      (position, index) => index === 0 || position - positions[index - 1] >= 30,
    ),
  );
});

test("additional equipment uses presence style without a planned quantity", () => {
  const markup = render(
    [point(0), point(1), point(2)],
    {
      id: "1",
      name: "Экскаватор",
      color: "#186d8f",
      planned: 0,
      values: [1, 1, 2],
    },
    "additional",
  );
  assert.match(
    markup,
    /Присутствие видов техники, не предусмотренных планом этапа/,
  );
  assert.match(markup, /Обнаружена вне плана/);
  assert.match(markup, /equipment-timeline-fill above/);
  assert.doesNotMatch(markup, /equipment-timeline-planned/);
});

test("smoothing step fills the interval to the next point in all three charts", () => {
  const points = [point(0), point(5), point(10)];
  const row = {
    id: "1",
    name: "Экскаватор",
    color: "#186d8f",
    planned: 1,
    values: [1, 1, 1],
    activity: points.map(() => ({
      working: 1,
      idle: 0,
      unknown: 0,
      continuedWorking: 0,
    })),
  };
  for (const mode of ["presence", "activity", "additional"]) {
    const markup = render(points, row, mode, 300);
    const widths = [
      ...markup.matchAll(
        /class="equipment-timeline-fill [^"]+" x="[\d.]+" y="[\d.]+" width="([\d.]+)"/g,
      ),
    ].map((match) => Number(match[1]));
    assert.ok(
      widths.some((width) => width > 100),
      mode,
    );
    assert.match(
      markup,
      /Открыть кадр измерения|equipment-timeline-dot|equipment-timeline-fill/,
    );
  }
});

const frameOutput = join(outputDir, "FrameDetections.mjs");
await esbuild.build({
  entryPoints: [
    fileURLToPath(new URL("../web/src/FrameDetections.tsx", import.meta.url)),
  ],
  outfile: frameOutput,
  bundle: true,
  platform: "node",
  format: "esm",
  packages: "external",
  jsx: "automatic",
});
const { FrameDetections } = await import(pathToFileURL(frameOutput).href);

test("held detections stay available in data but have no frame or label", () => {
  const markup = renderToStaticMarkup(
    React.createElement(FrameDetections, {
      detections: [
        {
          class_id: "1",
          confidence: 0.9,
          bbox: [0.1, 0.1, 0.3, 0.3],
          observed: false,
          missed_frames: 2,
        },
        {
          class_id: "2",
          confidence: 0.8,
          bbox: [0.4, 0.4, 0.6, 0.6],
          observed: true,
        },
      ],
      showFrames: true,
      showLabels: true,
      nameForClass: (id) => id,
      colorForClass: () => "#333",
    }),
  );
  assert.equal(count(markup, 'class="lab-detection show-frame"'), 1);
  assert.doesNotMatch(markup, /удержание/);
});
