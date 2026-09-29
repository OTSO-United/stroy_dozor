import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

test('source workflows cannot schedule the deployment runner', () => {
  assert.doesNotMatch(read('.github/workflows/ci.yml'), /self-hosted|stroykontur-deploy|repository_dispatch/);
  assert.equal(existsSync(new URL('../.github/workflows/deliver.yml', import.meta.url)), false);
});
test('private controller remains compatible with Free repository secrets', () => {
  const workflow = read('deployment/github/deploy.yml');
  assert.doesNotMatch(workflow, /^\s+environment:/m);
  assert.match(workflow, /secrets\.SOURCE_READ_TOKEN/);
  assert.match(workflow, /vars\.CD_CONFIG/);
  assert.match(workflow, /vars\.CD_ENABLED == 'true'/);
});

test('private workflows are restricted to main and have no pull-request triggers', () => {
  for (const path of ['deployment/github/deploy.yml', 'deployment/github/check-runner.yml']) {
    const workflow = read(path);
    assert.match(workflow, /github\.event\.repository\.private == true/);
    assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
    assert.doesNotMatch(workflow, /^\s+(pull_request(?:_target)?|push):/m);
    assert.match(workflow, /persist-credentials: false/);
    assert.match(workflow, /runs-on: \[self-hosted, Windows, X64, stroykontur-deploy\]/);
  }
});

test('manual runner diagnostic cannot invoke deployment or require source credentials', () => {
  const workflow = read('deployment/github/check-runner.yml');
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /scripts\/cd\/check-runner\.ps1/);
  assert.doesNotMatch(workflow, /deploy-verified|repository_dispatch|SOURCE_READ_TOKEN/);
});

test('delivery credential diagnostic stays on hosted runners without deployment', () => {
  const workflow = read('deployment/github/check-access.yml');
  assert.match(workflow, /runs-on: ubuntu-24\.04/);
  assert.match(workflow, /types: \[verified-main\]/);
  assert.match(workflow, /github\.event\.repository\.private == true/);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /vars\.CD_ENABLED != 'true'/);
  assert.match(workflow, /secrets\.SOURCE_READ_TOKEN/);
  assert.match(workflow, /ref: \$\{\{ steps\.gate\.outputs\.sha \}\}/);
  assert.match(workflow, /Source repository metadata: HTTP/);
  assert.ok(workflow.indexOf('Check source repository visibility') < workflow.indexOf('- id: gate'));
  assert.doesNotMatch(workflow, /self-hosted|deploy-verified|stroykontur-deploy|^\s+environment:/m);
  assert.doesNotMatch(workflow, /^\s+(pull_request(?:_target)?|push):/m);
});
