import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "../web/node_modules/typescript/lib/typescript.js";

function loadTypeScript(path) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const exports = {};
  runInNewContext(compiled, { exports, module: { exports } });
  return exports;
}

const { workPhase, compareWorkByCodeThenDate } = loadTypeScript("../web/src/workStage.ts");
const { bindFrameWheel } = loadTypeScript("../web/src/frameInteraction.ts");
const work = {
  starts_at: "2026-09-22T07:00:00Z",
  ends_at: "2026-09-22T17:00:00Z",
};

test("manual stage status takes priority over the schedule", () => {
  const afterEnd = Date.parse("2026-09-27T09:00:00Z");
  assert.equal(workPhase(work, "in_progress", afterEnd), "active");
  assert.equal(workPhase(work, "completed", afterEnd), "completed");
  assert.equal(workPhase(work, "planned", afterEnd), "overdue");
});

test("calendar stages are active only within their actual time window", () => {
  assert.equal(
    workPhase(work, "planned", Date.parse("2026-09-22T08:00:00Z")),
    "active",
  );
  assert.equal(
    workPhase(work, "planned", Date.parse("2026-09-21T08:00:00Z")),
    "upcoming",
  );
});

test("binding stages sort by hierarchical index, then start date", () => {
  const stages = [
    { id: "late", code: "12.3.7.1", starts_at: "2026-10-02T07:00:00Z" },
    { id: "early", code: "12.3.7.1", starts_at: "2026-09-28T07:00:00Z" },
    { id: "ten", code: "12.10", starts_at: "2026-09-01T07:00:00Z" },
    { id: "two", code: "12.2", starts_at: "2026-10-01T07:00:00Z" },
  ];
  assert.deepEqual(
    stages.sort(compareWorkByCodeThenDate).map((stage) => stage.id),
    ["two", "early", "late", "ten"],
  );
});

test("camera frame viewer uses full viewport and wheel zoom near pointer", () => {
  const viewer = readFileSync(new URL("../web/src/CameraFrameViewer.tsx", import.meta.url), "utf8");
  assert.match(viewer, /aria-modal="true"/);
  assert.match(viewer, /event\.preventDefault\(\)/);
  assert.match(viewer, /event\.clientX - bounds\.left/);
  assert.match(viewer, /current\.scrollLeft = anchor\.fractionX/);
  assert.match(viewer, /setPointerCapture/);
  assert.match(viewer, /FrameDetections/);
});

function wheelViewport() {
  let listener;
  let removed = false;
  const viewport = {
    clientHeight: 500,
    scrollLeft: 5,
    scrollTop: 10,
    getBoundingClientRect() {
      return { left: 20, top: 30 };
    },
    addEventListener(type, callback, options) {
      assert.equal(type, "wheel");
      assert.equal(options.passive, false);
      listener = callback;
    },
    removeEventListener(type, callback) {
      assert.equal(type, "wheel");
      assert.equal(callback, listener);
      removed = true;
    },
  };
  const fire = (changes) => {
    let prevented = false;
    listener({
      deltaX: 0,
      deltaY: 0,
      deltaMode: 0,
      ctrlKey: false,
      shiftKey: false,
      preventDefault() {
        prevented = true;
      },
      ...changes,
    });
    assert.equal(prevented, true);
  };
  return { viewport, fire, wasRemoved: () => removed };
}

test("wheel pans vertically, Ctrl zooms at pointer, Shift pans horizontally", () => {
  const view = wheelViewport();
  const zoomValues = [];
  const unbind = bindFrameWheel(view.viewport, 100, (value, anchor) =>
    zoomValues.push({ value, anchor }),
  );
  view.fire({ deltaY: -100 });
  assert.equal(view.viewport.scrollTop, -90);
  view.fire({ ctrlKey: true, deltaY: -30, clientX: 120, clientY: 230 });
  assert.equal(zoomValues[0].value, 125);
  assert.equal(zoomValues[0].anchor.x, 100);
  assert.equal(zoomValues[0].anchor.y, 200);
  view.fire({ shiftKey: true, deltaY: 20 });
  assert.equal(view.viewport.scrollLeft, 25);
  unbind();
  assert.equal(view.wasRemoved(), true);
});

test("Ctrl wheel zoom stays within bounds and native horizontal motion pans", () => {
  const view = wheelViewport();
  const zoomValues = [];
  bindFrameWheel(view.viewport, 400, (value) => zoomValues.push(value));
  view.fire({ ctrlKey: true, deltaY: -60, clientX: 20, clientY: 30 });
  assert.deepEqual(zoomValues, [400]);
  view.fire({ deltaX: 16, deltaY: 2 });
  assert.equal(view.viewport.scrollLeft, 21);
});
