// Render saved public cards with Sylvia's existing frontend; no mechanics or network.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const [sourceRoot, proofPath, output] = process.argv.slice(2);
global.window = {};
global.document = { addEventListener() {} };
eval(fs.readFileSync(path.join(sourceRoot, 'src-tauri/frontend/app.js'), 'utf8'));
const proof = JSON.parse(fs.readFileSync(proofPath, 'utf8').replace(/^\uFEFF/, ''));
const escape = value => String(value).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
let count = 0;
const items = (proof.cases || proof.steps).map(row => {
  const result = { status: 'committed', result_card: row.card, activity_label: row.activity_label };
  const html = window.SylviaGMFrontend.toolResultMarkup({tool_name: row.operation, result}, '');
  if (row.card) {
    assert(html.includes(`result-card-${row.card.footprint}`));
    assert(!html.includes('STALE_SUCCESS_SENTINEL'));
    assert(!html.includes('undefined') && !html.includes('NaN'));
    assert(!html.includes('<details open'));
    count += 1;
  }
  return `<section class="qa-case"><h2>${escape(row.operation)} · ${escape(row.variant || row.status || '')}</h2>${html}</section>`;
}).join('\n');
const css = fs.readFileSync(path.join(sourceRoot, 'src-tauri/frontend/app.css'), 'utf8');
const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>Saved TD results</title>
<style>${css}</style><style>body{display:block;margin:0;padding:24px;background:#11151d;color:#e8edf5}.qa-gallery{max-width:900px;margin:auto}.qa-case{margin:20px 0;padding:12px;border:1px solid #344052;border-radius:8px}.qa-case h2{font:14px sans-serif;color:#9ba9bf;margin:0 0 12px}</style>
<body><main class="qa-gallery"><h1>Saved TD results</h1><p>Existing Sylvia frontend renders actual isolated native results.</p>${items}</main></body></html>`;
assert(!fs.existsSync(output), 'Preserve an existing rendered artifact.');
fs.writeFileSync(output, html);
console.log(JSON.stringify({passed:true,rendered_cards:count,output,renderer:'existing src-tauri/frontend/app.js'}));
