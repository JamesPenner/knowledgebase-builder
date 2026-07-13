const _sources = {};

function _getStageModeFromDom(stage) {
  const btn = document.querySelector('[data-stage-mode-btn="' + stage + '"]');
  if (!btn) return 'resume';
  return btn.textContent.trim() === 'Re-run' ? 'rerun' : 'resume';
}

function _buildBody(stage, kb) {
  const scope = window.KB_SCOPE || {};
  const {source_id, folder_prefix, file_type, date_from, date_to, name_pattern} = scope;
  const run_mode = _getStageModeFromDom(stage);
  const body = {kb, run_mode, source_id, folder_prefix, file_type, date_from, date_to, name_pattern};
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

// Opens (or reuses) the SSE stream for (stage, kb) and wires the terminal
// done/failed handling shared by runStage() and cancelStage() — cancellation
// is cooperative server-side, so the only reliable "it actually stopped"
// signal is this stream reaching a terminal status, not the cancel POST itself.
function _attachStream(stage, kb) {
  if (_sources[stage]) return _sources[stage];

  const badge = document.getElementById('badge-' + stage);
  const es = new EventSource('/api/stages/' + stage + '/stream?kb=' + encodeURIComponent(kb));
  _sources[stage] = es;

  es.onmessage = function (e) {
    const d = JSON.parse(e.data);
    _renderProgress(stage, d);
    if (d.status === 'done') {
      if (badge) { badge.className = 'badge badge-done'; badge.textContent = 'done'; }
      const rb = document.getElementById('btn-run-' + stage);
      const cb = document.getElementById('btn-cancel-' + stage);
      if (rb) rb.disabled = false;
      if (cb) cb.style.display = 'none';
      es.close();
      delete _sources[stage];
      // Reload so gate banners reflect updated touchpoint state.
      // Suppressed during multi-stage runs (workbench.js sets this flag and reloads at plan end).
      if (!window.WB_RUNNING_PLAN) location.reload();
    } else if (d.status === 'failed') {
      if (badge) { badge.className = 'badge badge-failed'; badge.textContent = 'failed'; }
      const rb = document.getElementById('btn-run-' + stage);
      const cb = document.getElementById('btn-cancel-' + stage);
      if (rb) rb.disabled = false;
      if (cb) cb.style.display = 'none';
      const progEl = document.getElementById('progress-' + stage);
      if (progEl) progEl.innerHTML =
        '<span class="progress-detail" style="color:#991b1b">' +
        (d.message || 'Stage failed — check server logs') + '</span>';
      es.close();
      delete _sources[stage];
    }
  };

  es.onerror = function () { es.close(); delete _sources[stage]; };
  return es;
}

async function runStage(stage, kb) {
  const runBtn = document.getElementById('btn-run-' + stage);
  const cancelBtn = document.getElementById('btn-cancel-' + stage);
  const badge = document.getElementById('badge-' + stage);

  if (runBtn) runBtn.disabled = true;
  if (cancelBtn) cancelBtn.style.display = '';
  if (badge) { badge.className = 'badge badge-running'; badge.textContent = 'running'; }

  let body;
  try {
    body = _buildBody(stage, kb);
  } catch (err) {
    console.error('runStage: body build failed for', stage, err);
    if (runBtn) runBtn.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (badge) { badge.className = 'badge badge-pending'; badge.textContent = 'pending'; }
    return;
  }

  let resp;
  try {
    resp = await fetch('/api/stages/' + stage + '/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body,
    });
  } catch (err) {
    console.error('runStage: network error for', stage, err);
    if (runBtn) runBtn.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (badge) { badge.className = 'badge badge-failed'; badge.textContent = 'failed'; }
    return;
  }
  if (resp.status === 409) {
    // Already running for this (kb, stage) — likely started from another tab.
    // Attach to the real job's stream instead of treating this as a failure.
    console.warn('runStage: already running for', stage, kb);
  } else if (!resp.ok) {
    console.error('runStage: server error', resp.status, 'for', stage);
    if (runBtn) runBtn.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (badge) { badge.className = 'badge badge-failed'; badge.textContent = 'failed'; }
    return;
  }

  _attachStream(stage, kb);
}

function cancelStage(stage, kb) {
  fetch('/api/stages/' + stage + '/cancel?kb=' + encodeURIComponent(kb), {method: 'POST'});
  // Cancellation is cooperative — the worker only stops between files, so
  // don't reset the UI eagerly. Show "cancelling…" and let the stream's
  // existing done/failed handling (in _attachStream) do the real cleanup
  // once the background job actually terminates.
  const badge = document.getElementById('badge-' + stage);
  if (badge) { badge.className = 'badge badge-running'; badge.textContent = 'cancelling…'; }
  _attachStream(stage, kb);
}
