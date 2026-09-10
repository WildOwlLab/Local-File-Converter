/* Frontend for the local converter.
 *
 * Two-step upload: the file is sent once to be identified, and the server
 * holds the bytes under a token so choosing a format does not re-upload them.
 * If that token has been swept the server answers 409 and we transparently
 * send the file again -- the user never sees it.
 */
'use strict';

const POLL_MS = 500;

const el = (id) => document.getElementById(id);
const ui = {
  drop: el('drop'), input: el('file-input'),
  banner: el('tool-banner'),
  fileCard: el('file-card'), name: el('file-name'),
  type: el('file-type'), size: el('file-size'),
  mismatch: el('mismatch'), reset: el('reset-btn'),
  picker: el('picker'), target: el('target'),
  convert: el('convert-btn'), chainNote: el('chain-note'),
  progress: el('progress-card'), stage: el('stage'), pct: el('pct'), bar: el('bar'),
  result: el('result'), resultName: el('result-name'), download: el('download'),
  error: el('error'), errorSummary: el('error-summary'),
  errorDetails: el('error-details'), errorBody: el('error-body'),
};

let currentFile = null;
let uploadToken = null;
let chained = [];
let pollTimer = null;

/* ---------------------------------------------------------------- helpers */

function show(node, visible) { node.hidden = !visible; }

function humanSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/* A FastAPI HTTPException detail is either a string or an object we built. */
async function readError(response) {
  let detail;
  try { detail = (await response.json()).detail; } catch { detail = null; }
  if (detail && typeof detail === 'object') return detail;
  return { message: detail || `Request failed (${response.status})` };
}

function resetOutputs() {
  clearTimeout(pollTimer);
  show(ui.progress, false);
  show(ui.result, false);
  show(ui.error, false);
  show(ui.errorDetails, false);
}

function fullReset() {
  resetOutputs();
  currentFile = null;
  uploadToken = null;
  chained = [];
  ui.input.value = '';
  show(ui.fileCard, false);
  show(ui.picker, false);
  show(ui.drop, true);
}

function fail(summary, details) {
  resetOutputs();
  ui.errorSummary.textContent = summary;
  ui.errorBody.textContent = details || '';
  show(ui.errorDetails, Boolean(details));
  show(ui.error, true);
}

/* ------------------------------------------------------------ tool banner */

async function loadHealth() {
  try {
    const response = await fetch('/health');
    const health = await response.json();
    if (health.missing && health.missing.length) {
      ui.banner.textContent =
        `Not installed: ${health.missing.join(', ')}. `
        + 'Conversions needing those tools will be refused; everything else works.';
      show(ui.banner, true);
    }
  } catch {
    /* /health is advisory. A failure here must not block converting. */
  }
}

/* ------------------------------------------------------- step 1: identify */

async function identify(file) {
  resetOutputs();
  const body = new FormData();
  body.append('file', file);

  const response = await fetch('/convert', { method: 'POST', body });
  if (!response.ok) {
    const detail = await readError(response);
    fail(detail.message, detail.detected_type ? `Detected as: ${detail.detected_type}` : '');
    return;
  }

  const info = await response.json();
  currentFile = file;
  uploadToken = info.upload_token;
  chained = info.chained_targets || [];

  ui.name.textContent = info.filename;
  ui.type.textContent = info.detected_type;
  ui.size.textContent = humanSize(info.size_bytes);

  if (info.extension_mismatch) {
    ui.mismatch.textContent =
      `This file is named .${info.claimed_ext} but its contents are `
      + `${info.detected_ext.toUpperCase()}. It will be treated as `
      + `${info.detected_ext.toUpperCase()}.`;
  }
  show(ui.mismatch, Boolean(info.extension_mismatch));

  ui.target.innerHTML = '';
  const targets = info.suggested_targets || [];
  for (const target of targets) {
    const option = document.createElement('option');
    option.value = target;
    option.textContent = chained.includes(target)
      ? `${target.toUpperCase()} (two-step)`
      : target.toUpperCase();
    ui.target.append(option);
  }

  show(ui.drop, false);
  show(ui.fileCard, true);
  if (targets.length) {
    updateChainNote();
    ui.convert.disabled = false;
    show(ui.picker, true);
  } else {
    show(ui.picker, false);
    fail(`No conversions are available for ${info.detected_ext.toUpperCase()} files.`, '');
  }
}

function updateChainNote() {
  const isChained = chained.includes(ui.target.value);
  ui.chainNote.textContent = isChained
    ? 'This runs as two conversions, passing through an intermediate format.'
    : '';
  show(ui.chainNote, isChained);
}

/* -------------------------------------------------------- step 2: convert */

function conversionBody(target) {
  const body = new FormData();
  body.append('target_format', target);
  if (uploadToken) body.append('upload_token', uploadToken);
  else body.append('file', currentFile);
  return body;
}

async function startConversion() {
  if (!currentFile) return;
  const target = ui.target.value;
  resetOutputs();
  ui.convert.disabled = true;

  let response = await fetch('/convert', { method: 'POST', body: conversionBody(target) });

  // The held upload was swept. Send the bytes again rather than showing the
  // user an error about an implementation detail they never asked for.
  if (response.status === 409) {
    const detail = await readError(response);
    if (detail.reason === 'expired_token') {
      uploadToken = null;
      response = await fetch('/convert', { method: 'POST', body: conversionBody(target) });
    }
  }

  // Whether it succeeded or not, the token has now been claimed.
  uploadToken = null;

  if (!response.ok) {
    ui.convert.disabled = false;
    const detail = await readError(response);
    const extra = detail.supported_targets && detail.supported_targets.length
      ? `Supported targets: ${detail.supported_targets.join(', ').toUpperCase()}`
      : '';
    fail(detail.message, extra);
    return;
  }

  const job = await response.json();
  ui.stage.textContent = job.chained ? job.steps.join('  →  ') : `${job.detected_ext} → ${target}`;
  setProgress(0, false);
  show(ui.progress, true);
  poll(job.job_id);
}

/* Decision that matters: `measured` comes from the server and is true only
 * when the tool reported real progress. Everything else gets a moving bar
 * instead of a percentage nobody actually measured. */
function setProgress(percent, measured) {
  ui.bar.classList.toggle('indeterminate', !measured);
  if (measured) {
    ui.bar.style.width = `${percent}%`;
    ui.pct.textContent = `${percent}%`;
  } else {
    ui.bar.style.width = '';
    ui.pct.textContent = '';
  }
}

async function poll(jobId) {
  let job;
  try {
    const response = await fetch(`/status/${jobId}`);
    if (!response.ok) {
      const detail = await readError(response);
      ui.convert.disabled = false;
      fail(detail.message, '');
      return;
    }
    job = await response.json();
  } catch (err) {
    ui.convert.disabled = false;
    fail('Lost contact with the converter.', String(err));
    return;
  }

  if (job.stage) ui.stage.textContent = job.stage;
  setProgress(job.progress, job.measured);

  if (job.status === 'done') {
    resetOutputs();
    ui.convert.disabled = false;
    ui.resultName.textContent = job.output_name;
    ui.download.href = job.download_url;
    ui.download.setAttribute('download', job.output_name);
    show(ui.result, true);
    return;
  }
  if (job.status === 'failed') {
    ui.convert.disabled = false;
    fail(job.error || 'The conversion failed.', job.details);
    return;
  }
  pollTimer = setTimeout(() => poll(jobId), POLL_MS);
}

/* ------------------------------------------------------------------ wiring */

ui.drop.addEventListener('click', () => ui.input.click());
ui.drop.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); ui.input.click(); }
});
ui.input.addEventListener('change', () => {
  if (ui.input.files.length) identify(ui.input.files[0]);
});

for (const type of ['dragenter', 'dragover']) {
  ui.drop.addEventListener(type, (event) => {
    event.preventDefault();
    ui.drop.classList.add('over');
  });
}
for (const type of ['dragleave', 'drop']) {
  ui.drop.addEventListener(type, () => ui.drop.classList.remove('over'));
}
ui.drop.addEventListener('drop', (event) => {
  event.preventDefault();
  if (event.dataTransfer.files.length) identify(event.dataTransfer.files[0]);
});
// Dropping anywhere else must not make the browser navigate away from the app.
for (const type of ['dragover', 'drop']) {
  document.addEventListener(type, (event) => {
    if (!ui.drop.contains(event.target)) event.preventDefault();
  });
}

ui.target.addEventListener('change', updateChainNote);
ui.convert.addEventListener('click', startConversion);
ui.reset.addEventListener('click', fullReset);

loadHealth();
