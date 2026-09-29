import { appendFileSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const SOURCE_REPOSITORY = 'OTSO-United/stroikontur-source';

export function validateRun(run, { repository, workflowId, sha, runId }) {
  if (!/^[a-f0-9]{40}$/.test(sha) || !/^[1-9][0-9]*$/.test(String(runId))) throw Error('Invalid SHA/run ID');
  if (run.repository?.full_name !== repository || run.head_repository?.full_name !== repository ||
      run.event !== 'push' || run.head_branch !== 'main' || run.conclusion !== 'success' ||
      run.status !== 'completed' || run.head_sha !== sha || String(run.id) !== String(runId) ||
      run.workflow_id !== workflowId || run.path !== '.github/workflows/ci.yml') {
    throw Error('Run is not a successful source main push CI for this exact SHA');
  }
  if (!Number.isSafeInteger(run.run_number) || run.run_number < 1) throw Error('Invalid CI run number');
  return { sha, runId: String(run.id), runNumber: run.run_number };
}

export async function verify({ repository = SOURCE_REPOSITORY, sha, runId, token, request = fetch }) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) throw Error('Invalid repository');
  const get = async path => {
    const response = await request(`https://api.github.com/repos/${repository}/${path}`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw Error(`GitHub verification failed: HTTP ${response.status}`);
    return response.json();
  };
  if (!/^[a-f0-9]{40}$/.test(sha) || !/^[1-9][0-9]*$/.test(String(runId))) throw Error('Invalid SHA/run ID');
  const workflow = await get('actions/workflows/ci.yml');
  if (workflow.name !== 'CI' || workflow.state !== 'active') throw Error('Expected active CI workflow');
  const run = await get(`actions/runs/${runId}`);
  const result = validateRun(run, { repository, workflowId: workflow.id, sha, runId });
  const compare = await get(`compare/${sha}...main`);
  if (!['ahead', 'identical'].includes(compare.status) || compare.merge_base_commit?.sha !== sha) {
    throw Error('SHA is no longer reachable from source main');
  }
  return result;
}

async function main() {
  const env = process.env;
  const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, 'utf8'));
  const repository = SOURCE_REPOSITORY;
  let sha, runId;
  if (env.GITHUB_EVENT_NAME === 'workflow_run') {
    if (event.repository?.full_name !== repository) throw Error('Wrong event repository');
    sha = event.workflow_run.head_sha;
    runId = event.workflow_run.id;
  } else if (env.GITHUB_EVENT_NAME === 'repository_dispatch') {
    if (event.action !== 'verified-main') throw Error('Wrong dispatch type');
    ({ sha, runId } = event.client_payload);
  } else if (env.GITHUB_EVENT_NAME === 'workflow_dispatch') {
    ({ sha, runId } = event.inputs);
  } else { throw Error('Unsupported event'); }
  const result = await verify({ repository, sha, runId, token: env.SOURCE_READ_TOKEN });
  appendFileSync(env.GITHUB_OUTPUT, Object.entries(result).map(([k, v]) => `${k}=${v}\n`).join(''));
  if (env.DEPLOY_DISPATCH_REQUIRED === 'true' || env.DEPLOY_REPOSITORY) {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.DEPLOY_REPOSITORY) || env.DEPLOY_REPOSITORY === repository) throw Error('Separate deployment repository required');
    if (!env.DEPLOY_DISPATCH_TOKEN) throw Error('Deployment dispatch credential is missing');
    const metadata = await fetch(`https://api.github.com/repos/${env.DEPLOY_REPOSITORY}`, {
      signal: AbortSignal.timeout(30000),
      headers: { Authorization: `Bearer ${env.DEPLOY_DISPATCH_TOKEN}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    });
    if (!metadata.ok) throw Error(`Deployment repository verification failed: HTTP ${metadata.status}`);
    const target = await metadata.json();
    if (target.private !== true || target.default_branch !== 'main') throw Error('Deployment repository must be private with default branch main');
    const response = await fetch(`https://api.github.com/repos/${env.DEPLOY_REPOSITORY}/dispatches`, {
      method: 'POST', signal: AbortSignal.timeout(30000),
      headers: { Authorization: `Bearer ${env.DEPLOY_DISPATCH_TOKEN}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
      body: JSON.stringify({ event_type: 'verified-main', client_payload: result }),
    });
    if (response.status !== 204) throw Error(`Dispatch failed: HTTP ${response.status}`);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
