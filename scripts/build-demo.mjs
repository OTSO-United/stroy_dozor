import {readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const template = await readFile(path.join(root, 'frontend/mockup.html'), 'utf8');
const state = (await readFile(path.join(root, 'frontend/demo-state.mjs'), 'utf8')).replace(/^export /gm, '');
const ui = await readFile(path.join(root, 'frontend/demo-ui.js'), 'utf8');
const fragment = template.replace('<!-- DEMO_SCRIPT -->', '<script>\n(() => {\n' + state + '\n' + ui + '\n})();\n</script>');
if (fragment.includes('<!-- DEMO_SCRIPT -->')) throw new Error('Unresolved marker');
const standalone = '<!doctype html>\n<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>СтройДозор — демонстрация</title><style>body{margin:0;padding:18px;background:light-dark(#e9edef,#0e1115);color-scheme:light dark}body>section{max-width:1200px!important;margin:auto}@media(max-width:600px){body{padding:0}}</style></head><body>\n' + fragment + '\n</body></html>\n';
await writeFile(path.join(root, 'frontend/index.html'), standalone);
if (process.argv[2]) {
  const target = path.resolve(process.argv[2]);
  if (path.extname(target) !== '.html') throw new Error('Expected an HTML output path');
  await writeFile(target, fragment);
}
console.log('Built standalone demo' + (process.argv[2] ? ' and inline fragment.' : '.'));
