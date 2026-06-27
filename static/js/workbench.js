/* Workbench multi-stage orchestration */
const WB = (() => {

  // ---------------------------------------------------------------------------
  // Stage mode tracking
  // ---------------------------------------------------------------------------

  let _globalMode = 'resume';
  const _stageModes = {};   // per-stage override; null means "use global"

  function getStageMode(stage) {
    return _stageModes[stage] !== undefined ? _stageModes[stage] : _globalMode;
  }

  function setAllModes(mode) {
    _globalMode = mode;
    Object.keys(_stageModes).forEach(s => delete _stageModes[s]);
    // Update global toggle buttons
    document.querySelectorAll('.wb-mode-btn').forEach(btn => {
      btn.classList.toggle('wb-mode-btn--active', btn.dataset.mode === mode);
    });
    // Update all stage mode indicators
    document.querySelectorAll('[data-stage-mode-btn]').forEach(btn => {
      const stage = btn.dataset.stageModeBtn;
      _refreshStageModeBtn(stage);
    });
  }

  function toggleStageMode(stage) {
    const current = getStageMode(stage);
    if (stage === 'ingest') {
      _stageModes[stage] = current === 'incremental' ? 'full' : 'incremental';
    } else {
      _stageModes[stage] = current === 'rerun' ? 'resume' : 'rerun';
    }
    _refreshStageModeBtn(stage);
  }

  function _refreshStageModeBtn(stage) {
    const btn = document.querySelector(`[data-stage-mode-btn="${stage}"]`);
    if (!btn) return;
    const m = getStageMode(stage);
    if (stage === 'ingest') {
      btn.textContent = m === 'incremental' ? 'Incremental' : 'Full scan';
    } else {
      btn.textContent = m === 'rerun' ? 'Re-run' : 'Resume';
    }
  }

  // ---------------------------------------------------------------------------
  // Sources header collapse/expand
  // ---------------------------------------------------------------------------

  function toggleSources() {
    const body = document.getElementById('wb-sources-body');
    const arrow = document.getElementById('wb-sources-arrow');
    if (!body) return;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    if (arrow) arrow.textContent = open ? '▸' : '▾';
    try {
      const kb = window.KB_NAME || '';
      localStorage.setItem('kb-sources-open-' + kb, open ? '0' : '1');
    } catch (_) {}
  }

  function _initSources() {
    const body = document.getElementById('wb-sources-body');
    const arrow = document.getElementById('wb-sources-arrow');
    if (!body) return;
    const kb = window.KB_NAME || '';
    const sources = window.KB_SOURCES || [];

    let open;
    if (sources.length === 0) {
      open = true;
    } else {
      try {
        const stored = localStorage.getItem('kb-sources-open-' + kb);
        open = stored === '1';
      } catch (_) {
        open = false;
      }
    }
    body.style.display = open ? '' : 'none';
    if (arrow) arrow.textContent = open ? '▾' : '▸';
  }

  // ---------------------------------------------------------------------------
  // Scope bar
  // ---------------------------------------------------------------------------

  function getScope() {
    const srcSel = document.getElementById('scope-source');
    const typeSel = document.getElementById('scope-type');
    const setSel = document.getElementById('scope-set');
    const source_id = srcSel && srcSel.value ? parseInt(srcSel.value, 10) : null;
    const file_type = typeSel && typeSel.value ? typeSel.value : null;
    const set_id = setSel && setSel.value ? parseInt(setSel.value, 10) : null;
    return {source_id, file_type, set_id};
  }

  function onScopeChange() {
    window.KB_SCOPE = getScope();
  }

  function _initScopeBar() {
    const sources = window.KB_SOURCES || [];
    const sets = window.KB_SETS || [];

    const srcSel = document.getElementById('scope-source');
    if (srcSel) {
      if (sources.length >= 2) {
        sources.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = s.path;
          srcSel.appendChild(opt);
        });
        srcSel.closest('.wb-scope-bar-item').style.display = '';
      } else {
        const item = srcSel.closest('.wb-scope-bar-item');
        if (item) item.style.display = 'none';
      }
    }

    const setSel = document.getElementById('scope-set');
    if (setSel) {
      if (sets.length > 0) {
        sets.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = s.name + ' (' + s.file_count + ' files)';
          setSel.appendChild(opt);
        });
        setSel.closest('.wb-scope-bar-item').style.display = '';
      } else {
        const item = setSel.closest('.wb-scope-bar-item');
        if (item) item.style.display = 'none';
      }
    }

    window.KB_SCOPE = getScope();
  }

  // Keep for HTMX panel reload compatibility
  function reloadSets() { _initScopeBar(); }

  // ---------------------------------------------------------------------------
  // Checkbox / selection
  // ---------------------------------------------------------------------------

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
  // Stage execution
  // ---------------------------------------------------------------------------

  async function _runStageAsync(stage) {
    const kb = window.KB_NAME;
    await runStage(stage, kb);
    return new Promise((resolve, reject) => {
      const es = new EventSource('/api/stages/' + stage + '/stream');
      es.onmessage = function (e) {
        const d = JSON.parse(e.data);
        if (d.status === 'done') { es.close(); resolve('done'); }
        else if (d.status === 'failed') { es.close(); reject(new Error('Stage ' + stage + ' failed: ' + (d.message || ''))); }
      };
      es.onerror = function () { es.close(); reject(new Error('SSE error on stage ' + stage)); };
    });
  }

  async function _runPlan(stages) {
    const scope = getScope();
    window.KB_SCOPE = scope;
    const completed = [...(window.KB_CHECKPOINTS || [])];

    let plan;
    try {
      const resp = await fetch('/api/stages/resolve-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stages, completed}),
      });
      if (!resp.ok) { console.error('resolve-plan failed', await resp.text()); return; }
      plan = (await resp.json()).plan;
    } catch (err) { console.error('resolve-plan error', err); return; }

    window.WB_RUNNING_PLAN = true;
    const runnable = plan.filter(e => typeof e === 'string');
    for (const stage of runnable) {
      try {
        await _runStageAsync(stage);
        if (!window.KB_CHECKPOINTS.includes(stage)) window.KB_CHECKPOINTS.push(stage);
      } catch (err) { console.error('Stopping plan: ' + err.message); break; }
    }
    window.WB_RUNNING_PLAN = false;
    location.reload();
  }

  function runSelected() { _runPlan(_checkedStages()); }
  function runGroup(groupId) { _runPlan((window.KB_GROUP_STAGES || {})[groupId] || []); }
  function runAll() {
    const all = (window.KB_GROUPS || []).flatMap(id => (window.KB_GROUP_STAGES || {})[id] || []);
    _runPlan(all);
  }

  function toggleHelp(stage) {
    const row = document.getElementById('help-' + stage);
    const btn = document.getElementById('help-btn-' + stage);
    if (!row) return;
    const visible = row.style.display !== 'none';
    row.style.display = visible ? 'none' : '';
    if (btn) btn.textContent = visible ? '▸' : '▾';
  }

  document.addEventListener('DOMContentLoaded', function () {
    _initSources();
    _initScopeBar();
  });

  return {
    runSelected, runGroup, runAll, toggleHelp, onCheckChange, onScopeChange, getScope,
    toggleSources, setAllModes, toggleStageMode, getStageMode, reloadSets,
  };
})();
