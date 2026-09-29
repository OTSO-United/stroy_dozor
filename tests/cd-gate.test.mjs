import test from 'node:test';
import assert from 'node:assert/strict';
import { validateRun, verify } from '../scripts/cd/github-gate.mjs';

const sha = 'a'.repeat(40);
const options = { repository: 'OTSO-United/stroikontur-source', workflowId: 7, sha, runId: '123' };
const run = { repository: { full_name: options.repository }, head_repository: { full_name: options.repository },
  event: 'push', head_branch: 'main', status: 'completed', conclusion: 'success', head_sha: sha,
  id: 123, workflow_id: 7, path: '.github/workflows/ci.yml', run_number: 9 };

test('exact successful main push is accepted', () => {
  assert.deepEqual(validateRun(run, options), { sha, runId: '123', runNumber: 9 });
});
for (const [key, value] of Object.entries({ event: 'pull_request', head_branch: 'feature', conclusion: 'failure',
  status: 'in_progress', head_sha: 'b'.repeat(40), workflow_id: 8, path: '.github/workflows/fake.yml', id: 124,
  repository: { full_name: 'attacker/fork' }, head_repository: { full_name: 'attacker/fork' } })) {
  test(`rejects untrusted ${key}`, () => assert.throws(() => validateRun({ ...run, [key]: value }, options)));
}
test('rejects injected SHA and run ID', () => {
  assert.throws(() => validateRun(run, { ...options, sha: 'main;calc.exe' }));
  assert.throws(() => validateRun(run, { ...options, runId: '../../runs/123' }));
});
for (const key of ['repository', 'head_repository']) {
  test(`rejects the previous owner in ${key}`, () => {
    assert.throws(() => validateRun({ ...run, [key]: { full_name: 'example/other-source' } }, options));
  });
}
test('manual retry verifies original CI and reachability, retaining head_sha', async () => {
  const urls = [];
  const request = async url => {
    urls.push(url);
    const payload = url.includes('/compare/') ? { status: 'ahead', merge_base_commit: { sha } } :
      url.endsWith('/ci.yml') ? { id: 7, name: 'CI', state: 'active' } : run;
    return { ok: true, json: async () => payload };
  };
  assert.equal((await verify({ sha, runId: options.runId, token: 'test-only', request })).sha, sha);
  assert.deepEqual(urls, [
    'https://api.github.com/repos/OTSO-United/stroikontur-source/actions/workflows/ci.yml',
    'https://api.github.com/repos/OTSO-United/stroikontur-source/actions/runs/123',
    `https://api.github.com/repos/OTSO-United/stroikontur-source/compare/${sha}...main`,
  ]);
});
test('force-pushed-away commit fails closed', async () => {
  const request = async url => ({ ok: true, json: async () => url.includes('/compare/') ?
    { status: 'diverged', merge_base_commit: { sha: 'b'.repeat(40) } } :
    url.endsWith('/ci.yml') ? { id: 7, name: 'CI', state: 'active' } : run });
  await assert.rejects(verify({ ...options, token: 'test-only', request }), /reachable/);
});
test('GitHub access failure never authorizes deployment', async () => {
  await assert.rejects(verify({ ...options, request: async () => ({ ok: false, status: 403 }) }), /HTTP 403/);
});
