import { appendFileSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { verify } from './github-gate.mjs';

export function validateMigrationInputs(inputs) {
  const { sha, runId, expectedRevision, targetRevision, mode, operationId = '', recoveryAction = 'resume' } = inputs;
  if (!/^[a-f0-9]{40}$/.test(sha) || !/^[1-9][0-9]*$/.test(runId)) throw Error('Invalid source SHA/run ID');
  if (expectedRevision !== '003' || targetRevision !== '004') throw Error('Only reviewed 003 -> 004 is supported');
  if (!['check', 'release', 'recover'].includes(mode)) throw Error('Invalid mode');
  if (!['resume', 'restore-previous'].includes(recoveryAction)) throw Error('Invalid recovery action');
  if (mode === 'recover' ? !/^[a-f0-9]{32}$/.test(operationId) : operationId !== '') throw Error('Invalid operation ID for mode');
  if (mode !== 'recover' && recoveryAction !== 'resume') throw Error('Recovery action requires recovery mode');
  return { sha, runId, expectedRevision, targetRevision, mode, operationId, recoveryAction };
}

export async function gate(event, env, request = fetch) {
  if (env.GITHUB_EVENT_NAME !== 'workflow_dispatch' || env.GITHUB_REF !== 'refs/heads/main' ||
      event.repository?.full_name !== 'OTSO-United/stroykontur-deployment' || event.repository?.private !== true) {
    throw Error('Manual private controller main workflow required');
  }
  const inputs = validateMigrationInputs(event.inputs);
  const result = await verify({ sha: inputs.sha, runId: inputs.runId, token: env.SOURCE_READ_TOKEN, request });
  return { ...inputs, ...result };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const env = process.env;
  gate(JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, 'utf8')), env).then(result => {
    appendFileSync(env.GITHUB_OUTPUT, Object.entries(result).map(([k, v]) => `${k}=${v}\n`).join(''));
  }).catch(error => { console.error(error.message); process.exitCode = 1; });
}
