const _sources = {};

function _getStageModeFromDom(stage) {
  const btn = document.querySelector('[data-stage-mode-btn="' + stage + '"]');
  if (!btn) return stage === 'ingest' ? 'full' : 'resume';
  const text = btn.textContent.trim();
  if (stage === 'ingest') return text === 'Incremental' ? 'incremental' : 'full';
  return text === 'Re-run' ? 'rerun' : 'resume';
}

function _buildBody(stage, kb) {
  const scope = window.KB_SCOPE || {};
  if (stage === 'ingest') {
    const incremental = _getStageModeFromDom('ingest') === 'incremental';
    return JSON.stringify({kb, incremental});
  }
  const {source_id, file_type, set_id} = scope;
  const run_mode = _getStageModeFromDom(stage);
  const body = {kb, run_mode, source_id, file_type, set_id};
  if (stage === 'suggest') {
    body.levels = ['a', 'b'];
  }
  return JSON.stringify(body);
}

function _fmtEta(seconds) {
  if (!seconds || seconds <= 0) return '';
  if (seconds < 60) return seconds + 's';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m + 'm' + (s > 0 ? ' ' + s + 's' : '');
}

function _renderProgress(stage, d) {
  const el = document.getElementById('progress-' + stage);
  if (!el) return;

  if (d.status === 'done') {
    el.innerHTML = '';
    return;
  }
  if (d.status !== 'running') return;  // 'failed' handled separately in onmessage

  const indeterminate = !d.total;
  const pct = indeterminate ? 0 : Math.round(d.current / d.total * 100);
  const trackClass = 'progress-bar-track' + (indeterminate ? ' progress-bar--indeterminate' : '');
  const fillStyle = indeterminate ? '' : ` style="width:${pct}%"`;

  const msg    = d.message || 'Starting…';
  const count  = d.total > 0 ? ` · ${d.current} / ${d.total}` : '';
  const eta    = _fmtEta(d.eta);
  const detail = msg + count + (eta ? ' · ETA ' + eta : '');

  el.innerHTML =
    `<div class="${trackClass}"><div class="progress-bar-fill"${fillStyle}></div></div>` +
    `<span class="progress-detail">${detail}</span>`;
}

async function runStage(stage, kb) {
  const runBtn = document.getElementById('btn-run-' + stage);
  const cancelBtn = document.getElementById('btn-cancel-' + stage);
  const badge = document.getElementById('badge-' + stage);

  runBtn.disabled = true;
  cancelBtn.style.display = '';
  badge.className = 'badge badge-running';
  badge.textContent = 'running';

  let body;
  try {
    body = _buildBody(stage, kb);
  } catch (err) {
    console.error('runStage: body build failed for', stage, err);
    runBtn.disabled = false;
    cancelBtn.style.display = 'none';
    badge.className = 'badge badge-pending';
    badge.textContent = 'pending';
    return;
  }

  await fetch('/api/stages/' + stage + '/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body,
  });

  const es = new EventSource('/api/stages/' + stage + '/stream');
  _sources[stage] = es;

  es.onmessage = function (e) {
    const d = JSON.parse(e.data);
    _renderProgress(stage, d);
    if (d.status === 'done') {
      badge.className = 'badge badge-done';
      badge.textContent = 'done';
      document.getElementById('btn-run-' + stage).disabled = false;
      document.getElementById('btn-cancel-' + stage).style.display = 'none';
      es.close();
      delete _sources[stage];
      // Reload so gate banners reflect updated touchpoint state.
      // Suppressed during multi-stage runs (workbench.js sets this flag and reloads at plan end).
      if (!window.WB_RUNNING_PLAN) location.reload();
    } else if (d.status === 'failed') {
      badge.className = 'badge badge-failed';
      badge.textContent = 'failed';
      document.getElementById('btn-run-' + stage).disabled = false;
      document.getElementById('btn-cancel-' + stage).style.display = 'none';
      const progEl = document.getElementById('progress-' + stage);
      if (progEl) progEl.innerHTML =
        '<span class="progress-detail" style="color:#991b1b">' +
        (d.message || 'Stage failed — check server logs') + '</span>';
      es.close();
      delete _sources[stage];
    }
  };

  es.onerror = function () { es.close(); delete _sources[stage]; };
}

function cancelStage(stage) {
  fetch('/api/stages/' + stage + '/cancel', {method: 'POST'});
  if (_sources[stage]) { _sources[stage].close(); delete _sources[stage]; }
  const badge = document.getElementById('badge-' + stage);
  badge.className = 'badge badge-pending';
  badge.textContent = 'pending';
  document.getElementById('btn-run-' + stage).disabled = false;
  document.getElementById('btn-cancel-' + stage).style.display = 'none';
  document.getElementById('progress-' + stage).innerHTML = '';
}
