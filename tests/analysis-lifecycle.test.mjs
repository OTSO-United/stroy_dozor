import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "../web/node_modules/typescript/lib/typescript.js";

const exports = {};
runInNewContext(
  ts.transpileModule(
    readFileSync(
      new URL("../web/src/analysisNotices.ts", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText,
  { exports, module: { exports } },
);
const {
  analysisNotices: collect,
  emptyNotices,
  enqueueNotices,
  advanceNotices,
  dismissNotice,
  noticeText,
  MAX_VISIBLE,
  MAX_PENDING,
} = exports;
const project = { id: "site-a", name: "Объект A" };
const analysisNotices = (jobs, seen, since) =>
  collect(jobs, seen, since, project);
const since = Date.parse("2026-09-28T06:00:00Z");
const job = {
  id: "one",
  kind: "analyze",
  status: "queued",
  updated_at: "2026-09-28T05:00:00Z",
  works: [{ id: "stage", code: "12.2", title: "Фундамент" }],
};
const started = {
  ...job,
  status: "running",
  started_at: "2026-09-28T06:01:00Z",
};

test("queued, failed-before-start and cancelled-before-start never announce success", () => {
  for (const status of ["queued", "failed", "cancelled"])
    assert.equal(
      analysisNotices([{ ...job, status }], new Set(), since).length,
      0,
    );
});
test("worker start and all-assessments readiness each notify once", () => {
  const seen = new Set();
  assert.equal(
    analysisNotices([started], seen, since)[0].type,
    "analysis_started",
  );
  assert.equal(analysisNotices([started], seen, since).length, 0);
  assert.equal(
    analysisNotices(
      [{ ...started, status: "succeeded", results_ready: false }],
      seen,
      since,
    ).length,
    0,
  );
  assert.equal(
    analysisNotices(
      [{ ...started, status: "succeeded", results_ready: true }],
      seen,
      since,
    )[0].type,
    "analysis_ready",
  );
  const restored = new Set(JSON.parse(JSON.stringify([...seen])));
  assert.equal(
    analysisNotices(
      [{ ...started, status: "succeeded", results_ready: true }],
      restored,
      since,
    ).length,
    0,
  );
});
test("parallel sources, several stages and reruns keep their own snapshot", () => {
  const second = {
    ...started,
    id: "two",
    works: [...started.works, { id: "other", code: "13.1", title: "Монтаж" }],
  };
  const seen = new Set();
  const messages = analysisNotices([second, started], seen, since);
  assert.equal(messages.length, 3);
  assert.equal(messages[2].work.title, "Монтаж");
  assert.equal(
    analysisNotices([{ ...started, id: "rerun" }], seen, since).length,
    1,
  );
});
test("continuous stream never announces completion and historical jobs stay quiet", () => {
  const seen = new Set();
  analysisNotices([started], seen, since);
  assert.equal(
    analysisNotices(
      [
        {
          ...started,
          status: "succeeded",
          continuous: true,
          results_ready: true,
        },
      ],
      seen,
      since,
    ).length,
    0,
  );
  assert.equal(
    analysisNotices(
      [{ ...started, results_ready: true }],
      new Set(),
      since + 120000,
    ).length,
    0,
  );
});

const event = (id, type = "analysis_ready", projectId = "site-a") => ({
  key: `${type}:${projectId}:run-${id}:stage-${id}`,
  type,
  projectId,
  projectName: projectId,
  runId: `run-${id}`,
  work: { id: `stage-${id}`, code: "12.2", title: "Этап" },
});

test("a run already in progress on page open announces later readiness", () => {
  const seen = new Set();
  const beforeOpen = { ...started, started_at: "2026-09-28T05:00:00Z" };
  assert.equal(analysisNotices([beforeOpen], seen, since).length, 0);
  const events = analysisNotices(
    [{ ...beforeOpen, status: "succeeded", results_ready: true }],
    seen,
    since,
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "analysis_ready");
});
for (const count of [1, 3, 20])
  test(`${count} completions merge once and expire at first deadline`, () => {
    const events = Array.from({ length: count }, (_, i) => event(i));
    let state = enqueueNotices(emptyNotices(), events, 100);
    assert.equal(state.visible.length, 1);
    assert.equal(state.visible[0].events.length, count);
    state = enqueueNotices(state, events, 6000);
    assert.equal(state.visible[0].events.length, count);
    state = enqueueNotices(state, [event("new")], 7000);
    assert.equal(state.visible[0].expiresAt, 7100);
    assert.equal(advanceNotices(state, 7100).visible.length, 0);
  });

test("errors preempt success, updates do not starve waiting groups, close advances queue", () => {
  let state = enqueueNotices(
    emptyNotices(),
    [event(1), event(2, "analysis_started"), event(3, "saved")],
    0,
  );
  state = enqueueNotices(
    state,
    [
      event(4, "error"),
      event(5, "analysis_failed"),
      event(6, "analysis_cancelled"),
    ],
    100,
  );
  assert.equal(state.visible.length, MAX_VISIBLE);
  assert.equal(state.pending.length, MAX_PENDING);
  assert(state.visible.some((g) => g.type === "error"));
  assert(state.visible.some((g) => g.type === "analysis_failed"));
  for (let i = 0; i < 20; i++) {
    state = enqueueNotices(state, [event(`more-${i}`)], 200 + i);
    assert(state.visible.length <= 3 && state.pending.length <= 3);
  }
  const failed = state.visible.find((g) => g.type === "analysis_failed");
  state = dismissNotice(state, failed.id, 1000);
  assert(!state.visible.some((g) => g.id === failed.id));
  assert.equal(state.visible.length, 3);
  state = advanceNotices(state, 12100);
  assert(!state.visible.some((g) => g.type === "error"));
  state = advanceNotices(state, 30000);
  assert.equal(state.visible.length + state.pending.length, 0);
});

test("waiting overflow keeps priorities and FIFO", () => {
  // Extra semantic kinds exercise the bound when the event vocabulary grows.
  let state = enqueueNotices(
    emptyNotices(),
    Array.from({ length: 20 }, (_, i) => event(i, `kind-${i}`)),
    0,
  );
  assert.equal(state.visible.length, 3);
  assert.equal(state.pending.length, 3);
  assert.equal(state.pending[0].type, "kind-3");
  state = enqueueNotices(state, [event("failure", "error")], 1);
  assert(state.visible.some((g) => g.type === "error"));
  assert.equal(state.pending.length, 3);
});

test("group preserves project/run/stage identities and names different projects", () => {
  const state = enqueueNotices(
    emptyNotices(),
    [event(1), event(1, "analysis_ready", "site-b")],
    0,
  );
  assert.equal(state.visible[0].events.length, 2);
  assert.match(
    noticeText(state.visible[0]).summary,
    /2 объекта: site-a, site-b/,
  );
  assert.equal(state.visible[0].events[1].runId, "run-1");
});

test("failure and cancellation cannot announce ready even with a stale readiness flag", () => {
  for (const status of ["failed", "cancelled"]) {
    const events = analysisNotices(
      [
        {
          ...started,
          status,
          results_ready: true,
          updated_at: started.started_at,
        },
      ],
      new Set(),
      since,
    );
    assert(!events.some((e) => e.type === "analysis_ready"));
    assert(events.some((e) => e.type === `analysis_${status}`));
  }
});

test("polling after close and session restoration does not resurrect events", () => {
  const seen = new Set();
  const events = analysisNotices([started], seen, since);
  let state = enqueueNotices(emptyNotices(), events, 0);
  state = dismissNotice(state, state.visible[0].id, 1);
  const restored = new Set(JSON.parse(JSON.stringify([...seen])));
  assert.equal(analysisNotices([started], restored, since).length, 0);
  assert.equal(state.visible.length, 0);
  assert.equal(
    analysisNotices([{ ...started, id: "rerun" }], restored, since).length,
    1,
  );
});
