import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gate, validateMigrationInputs } from '../scripts/cd/migration-gate.mjs';
import { validateRun } from '../scripts/cd/github-gate.mjs';

const sha = 'a'.repeat(40);
const inputs = { sha, runId: '123', expectedRevision: '003', targetRevision: '004', mode: 'check', operationId: '', recoveryAction: 'resume' };
const event = { repository: { full_name: 'OTSO-United/stroykontur-deployment', private: true }, inputs };
const env = { GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_REF: 'refs/heads/main', SOURCE_READ_TOKEN: 'test' };
const run = { repository: { full_name: 'OTSO-United/stroikontur-source' }, head_repository: { full_name: 'OTSO-United/stroikontur-source' },
  event: 'push', head_branch: 'main', status: 'completed', conclusion: 'success', head_sha: sha, id: 123,
  workflow_id: 7, path: '.github/workflows/ci.yml', run_number: 42 };
const request = async url => ({ ok: true, json: async () => url.includes('/compare/') ? { status: 'ahead', merge_base_commit: { sha } } :
  url.endsWith('/ci.yml') ? { id: 7, name: 'CI', state: 'active' } : run });

test('migration gate gets run number from API, never inputs', async () => {
  const result = await gate({ ...event, inputs: { ...inputs, runNumber: 999 } }, env, request);
  assert.equal(result.runNumber, 42);
  assert.equal(result.sha, sha);
});
for (const [name, value] of Object.entries({ sha: 'main; calc.exe', runId: '1\n2', expectedRevision: '002', targetRevision: 'head', mode: 'shell', operationId: '../pending', recoveryAction: 'downgrade' })) {
  test(`migration rejects unsafe ${name}`, () => assert.throws(() => validateMigrationInputs({ ...inputs, [name]: value })));
}
test('recovery requires bounded ID and the same gate', async () => {
  assert.throws(() => validateMigrationInputs({ ...inputs, mode: 'recover' }));
  const recovery = { ...inputs, mode: 'recover', operationId: 'b'.repeat(32), recoveryAction: 'restore-previous' };
  assert.equal((await gate({ ...event, inputs: recovery }, env, request)).operationId, recovery.operationId);
  await assert.rejects(gate({ ...event, inputs: recovery }, env, async () => ({ ok: false, status: 403 })));
});
for (const [name, change] of [
  ['PR event', { GITHUB_EVENT_NAME: 'pull_request' }], ['non-main controller', { GITHUB_REF: 'refs/heads/feature' }],
]) {
  test(`migration gate rejects ${name}`, async () => assert.rejects(gate(event, { ...env, ...change }, request)));
}
test('public or wrong controller repository cannot use server runner', async () => {
  await assert.rejects(gate({ ...event, repository: { ...event.repository, private: false } }, env, request));
  await assert.rejects(gate({ ...event, repository: { ...event.repository, full_name: 'OTSO-United/stroikontur-source' } }, env, request));
});
test('invalid API run numbers are rejected', () => {
  for (const value of [0, -1, 1.5, '42', Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => validateRun({ ...run, run_number: value }, { repository: 'OTSO-United/stroikontur-source', workflowId: 7, sha, runId: '123' }));
  }
});
test('migration workflow is manual, gates twice and does not interpolate inputs into shell', () => {
  const workflow = readFileSync(new URL('../deployment/github/deploy-migrations.yml', import.meta.url), 'utf8');
  assert.match(workflow, /workflow_dispatch:/);
  assert.doesNotMatch(workflow, /^\s+(pull_request|pull_request_target|repository_dispatch|push|concurrency|environment):/m);
  assert.equal((workflow.match(/migration-gate\.mjs/g) || []).length, 2);
  assert.match(workflow, /ref: \$\{\{ github\.sha \}\}/);
  assert.match(workflow, /ref: \$\{\{ needs\.verify\.outputs\.sha \}\}/);
  assert.doesNotMatch(workflow, /run:.*\$\{\{/);
  assert.match(workflow, /if: always\(\)/);
});
test('ordinary deployment shares lock and explicitly rejects migration recovery', () => {
  const ordinary = readFileSync(new URL('../scripts/cd/deploy-verified.ps1', import.meta.url), 'utf8');
  const migrations = readFileSync(new URL('../scripts/cd/deploy-migrations.ps1', import.meta.url), 'utf8');
  assert.match(ordinary, /Enter-DeploymentLock \$cfg.StateRoot/);
  assert.match(migrations, /Enter-DeploymentLock \$cfg.StateRoot/);
  assert.match(ordinary, /image-only recovery is forbidden/);
  assert.match(ordinary, /Migration changes require a separate reviewed migration\/restore procedure/);
});
