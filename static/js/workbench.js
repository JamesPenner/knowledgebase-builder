/* Workbench multi-stage orchestration */
const WB = (() => {

  function _checkedStages() {
    return Array.from(document.querySelectorAll('input[type=checkbox][id^="check-"]'))
      .filter(cb => cb.checked && !cb.disabled)
      .map(cb => cb.id.replace('check-', ''));
  }

  function onCheckChange() {
    const btn = document.getElementById('btn-run-selected');
    if (btn) btn.disabled = _checkedStages().length === 0;
  }

  // ---------------------------------------------------------------------------
  // Scope selector
  // ---------------------------------------------------------------------------

  function getScope() {
    const modeEl = document.getElementById('scope-mode');
    const mode = modeEl ? modeEl.value : 'resume';
    let source_id = null;
    let file_type = null;
    if (mode === 'by_source') {
      const sel = document.getElementById('scope-source');
      source_id = sel && sel.value ? parseInt(sel.value, 10) : null;
    }
    if (mode === 'by_type') {
      const checked = [...document.querySelectorAll('#scope-type input[type=checkbox]:checked')]
        .map(e => e.value);
      file_type = checked.length ? checked.join(',') : null;
    }
    return {scope_mode: mode, source_id, file_type};
  }

  function _updateScopeSummary() {
    const sc = window.KB_SCOPE || {};
    const el = document.getElementById('scope-summary');
    if (!el) return;
    if (sc.scope_mode === 'resume') {
      el.textContent = 'Processing: all pending files';
    } else if (sc.scope_mode === 'rerun') {
      el.textContent = 'Processing: all files (resetting stage first)';
    } else if (sc.scope_mode === 'new_files') {
      el.textContent = 'Processing: new files only (runs ingest first)';
    } else if (sc.scope_mode === 'by_source') {
      const src = (window.KB_SOURCES || []).find(s => s.id === sc.source_id);
      el.textContent = src ? 'Processing: source ' + src.path : 'Processing: selected source';
    } else if (sc.scope_mode === 'by_type') {
      el.textContent = 'Processing: ' + (sc.file_type || 'all types') + ' files';
    }
  }

  function onScopeChange() {
    const mode = document.getElementById('scope-mode').value;
    const srcSel = document.getElementById('scope-source');
    const typeEl = document.getElementById('scope-type');
    if (srcSel) srcSel.style.display = mode === 'by_source' ? '' : 'none';
    if (typeEl) typeEl.style.display = mode === 'by_type' ? '' : 'none';
    window.KB_SCOPE = getScope();
    _updateScopeSummary();
  }

  function _loadSources() {
    const sources = window.KB_SOURCES || [];
    const sel = document.getElementById('scope-source');
    if (!sel) return;
    sources.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.path;
      sel.appendChild(opt);
    });
  }

  // ---------------------------------------------------------------------------
  // Stage execution
  // ---------------------------------------------------------------------------

  /* Returns a Promise that resolves with 'done' or rejects with 'failed'
     when the stage's SSE stream completes. Delegates the actual run to
     the existing runStage() from pipeline.js but intercepts its EventSource
     via a wrapper EventSource that resolves on completion. */
  function _runStageAsync(stage) {
    return new Promise((resolve, reject) => {
      const kb = window.KB_NAME;

      /* Patch: after runStage fires, attach a second listener to the stream
         to get the terminal status. runStage already manages the badge/button
         UI; we just need to know when it finishes. */
      runStage(stage, kb);

      /* Poll the stream independently so we can await completion */
      const es = new EventSource('/api/stages/' + stage + '/stream');
      es.onmessage = function (e) {
        const d = JSON.parse(e.data);
        if (d.status === 'done') {
          es.close();
          resolve('done');
        } else if (d.status === 'failed') {
          es.close();
          reject(new Error('Stage ' + stage + ' failed: ' + (d.message || '')));
        }
      };
      es.onerror = function () {
        es.close();
        reject(new Error('SSE error on stage ' + stage));
      };
    });
  }

  async function _runPlan(stages) {
    const scope = getScope();
    window.KB_SCOPE = scope;
    const completed = [...(window.KB_CHECKPOINTS || [])];

    /* For new_files mode: force ingest to run (even if already completed) */
    let planStages = [...stages];
    let effectiveCompleted = completed;
    if (scope.scope_mode === 'new_files') {
      if (!planStages.includes('ingest')) planStages = ['ingest', ...planStages];
      effectiveCompleted = completed.filter(s => s !== 'ingest');
    }

    let plan;
    try {
      const resp = await fetch('/api/stages/resolve-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stages: planStages, completed: effectiveCompleted}),
      });
      if (!resp.ok) {
        console.error('resolve-plan failed', await resp.text());
        return;
      }
      plan = (await resp.json()).plan;
    } catch (err) {
      console.error('resolve-plan error', err);
      return;
    }

    /* Filter plan to runnable stages (strings, not touchpoint dicts) */
    const runnable = plan.filter(e => typeof e === 'string');

    for (const stage of runnable) {
      try {
        await _runStageAsync(stage);
        /* Add to local checkpoints so subsequent dep resolution in this
           session is accurate. */
        if (!window.KB_CHECKPOINTS.includes(stage)) {
          window.KB_CHECKPOINTS.push(stage);
        }
      } catch (err) {
        console.error('Stopping plan: ' + err.message);
        break;
      }
    }
  }

  function runSelected() {
    _runPlan(_checkedStages());
  }

  function runGroup(groupId) {
    const stages = (window.KB_GROUP_STAGES || {})[groupId] || [];
    _runPlan(stages);
  }

  function runAll() {
    const all = (window.KB_GROUPS || []).flatMap(
      id => (window.KB_GROUP_STAGES || {})[id] || []
    );
    _runPlan(all);
  }

  function toggleHelp(stage) {
    const row = document.getElementById('help-' + stage);
    const btn = document.querySelector(`[onclick="WB.toggleHelp('${stage}')"]`);
    if (!row) return;
    const visible = row.style.display !== 'none';
    row.style.display = visible ? 'none' : '';
    if (btn) btn.textContent = visible ? '▸' : '▾';
  }

  /* Initialise on page load */
  document.addEventListener('DOMContentLoaded', function () {
    _loadSources();
    window.KB_SCOPE = getScope();
  });

  return {runSelected, runGroup, runAll, toggleHelp, onCheckChange, onScopeChange, getScope};
})();
