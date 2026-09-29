// Targeted domain checks, NOT a full JSON Schema implementation or backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const schema = JSON.parse(await readFile(new URL('../contracts/snapshot-assessment.schema.json', import.meta.url)));
const examples = JSON.parse(await readFile(new URL('../contracts/snapshot-examples.json', import.meta.url)));

function check(value) {
  for (const key of schema.required) assert.ok(Object.hasOwn(value, key), key);
  assert.equal(value.schema_version, '0.2');
  assert.equal(value.scope, 'snapshot');
  assert.equal(value.observed_machine_seconds, null);
  assert.ok(['ready', 'partial', 'not_evaluated'].includes(value.readiness));
  assert.ok(['sparse_snapshots', 'insufficient_data'].includes(value.mode));
  assert.ok(value.limitations.length > 0);
  if (value.readiness === 'not_evaluated') {
    assert.equal(value.findings.length, 0);
    assert.ok(value.unknown_checks.length > 0);
  } else {
    assert.ok(value.binding_version && value.rule_bundle_version && value.context.version);
    assert.ok(['manual_scenario', 'schedule'].includes(value.context.basis));
    if (value.context.basis === 'schedule') assert.ok(value.captured_at && Number.isFinite(Date.parse(value.captured_at)));
  }
  if (value.context.basis === 'unknown') {
    assert.equal(value.readiness, 'not_evaluated');
    assert.equal(value.context.version, null);
    assert.equal(value.context.complete_work_set, false);
  }
  for (const finding of value.findings) {
    assert.equal(finding.evidence.media_id, value.media_id);
    if (finding.type === 'potential_missing') {
      assert.equal(finding.evidence.kind, 'view_region');
      for (const key of ['class_supported', 'zone_visible', 'snapshot_expectation_confirmed', 'required_group_not_observed']) assert.ok(finding.premises.includes(key));
    } else {
      assert.equal(finding.type, 'potential_unexpected');
      assert.equal(finding.evidence.kind, 'detection');
      assert.equal(value.context.complete_work_set, true);
      for (const key of ['class_and_zone_confident', 'complete_work_set', 'not_allowed_by_any_active_work', 'no_applicable_exception']) assert.ok(finding.premises.includes(key));
    }
  }
}
for (const example of examples) test('snapshot fixture: ' + example.case, () => check(example.value));
test('missing and unexpected coexist', () => assert.equal(examples[0].value.findings.length, 2));
test('manual scenario needs no invented timestamp', () => assert.equal(examples[0].value.captured_at, null));
test('reject snapshot machine time', () => {
  const value=structuredClone(examples[0].value); value.observed_machine_seconds=0;
  assert.throws(() => check(value));
});
test('reject absence without valid visibility', () => {
  const value=structuredClone(examples[0].value); value.findings[0].premises=[];
  assert.throws(() => check(value));
});
test('reject unexpected with incomplete work set', () => {
  const value=structuredClone(examples[0].value); value.context.complete_work_set=false;
  assert.throws(() => check(value));
});
test('reject historical schedule assessment with unknown event time', () => {
  const value=structuredClone(examples[0].value); value.context.basis='schedule';
  assert.throws(() => check(value));
});
test('reject missing observation lineage', () => {
  const value=structuredClone(examples[0].value); delete value.observation_id;
  assert.throws(() => check(value));
});
