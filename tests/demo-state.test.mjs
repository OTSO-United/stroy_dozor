import test from 'node:test';
import assert from 'node:assert/strict';
import {getDemoState, reviewKey} from '../frontend/demo-state.mjs';

test('unavailable camera never becomes zero equipment or business deviation', () => {
  for (const mode of ['dense_video', 'sparse_snapshots']) {
    const state = getDemoState({scenario: 'unavailable', mode});
    assert.equal(state.status, 'insufficient_evidence');
    assert.equal(state.mode, 'insufficient_data');
    assert.equal(state.machineCount, null);
    assert.equal(state.resourceCount, null);
    assert.equal(state.presenceSampleRatio, null);
    assert.equal(state.observedMachineSeconds, null);
  }
});
test('sparse snapshots never create continuous machine time', () => {
  for (const scenario of ['missing', 'normal']) {
    const state = getDemoState({scenario, mode: 'sparse_snapshots'});
    assert.equal(state.observedMachineSeconds, null);
    assert.equal(state.status, 'insufficient_evidence');
  }
});
test('observed zero is distinct from unknown', () => {
  const state = getDemoState({scenario: 'missing'});
  assert.equal(state.resourceCount, 0);
  assert.equal(state.status, 'potential_mismatch');
});
test('positive evidence is not claimed physical completion', () => {
  const state = getDemoState({scenario: 'normal'});
  assert.equal(state.status, 'supports_plan');
  assert.match(state.explanation, /не подтверждает объём/);
  assert.equal(Object.hasOwn(state, 'completionPercent'), false);
});
test('reviews are scoped to scenario, work, mode and revision', () => {
  assert.notEqual(reviewKey(getDemoState()), reviewKey(getDemoState({work: 'concrete'})));
  assert.notEqual(reviewKey(getDemoState()), reviewKey(getDemoState({mode: 'sparse_snapshots'})));
});
test('all 12 demo combinations are explicitly synthetic', () => {
  for (const scenario of ['missing', 'normal', 'unavailable'])
    for (const mode of ['dense_video', 'sparse_snapshots'])
      for (const work of ['earth', 'concrete']) assert.equal(getDemoState({scenario, mode, work}).synthetic, true);
});
test('unsupported input is rejected instead of silently mislabelled', () => {
  assert.throws(() => getDemoState({scenario: 'broken'}));
  assert.throws(() => getDemoState({mode: 'one-fps-always-safe'}));
  assert.throws(() => getDemoState({work: 'unknown'}));
});
