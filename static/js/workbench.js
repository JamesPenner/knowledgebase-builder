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
    document.querySelectorAll('.wb-seg-opt').forEach(btn => {
      btn.classList.toggle('wb-seg-opt--active', btn.dataset.mode === mode);
    });
    document.querySelectorAll('[data-stage-mode-btn]').forEach(btn => {
      const stage = btn.dataset.stageModeBtn;
      _refreshStageModeBtn(stage);
    });
  }

  function toggleStageMode(stage) {
    const current = getStageMode(stage);
    _stageModes[stage] = current === 'rerun' ? 'resume' : 'rerun';
    _refreshStageModeBtn(stage);
  }

  function _refreshStageModeBtn(stage) {
    const btn = document.querySelector(`[data-stage-mode-btn="${stage}"]`);
    if (!btn) return;
    btn.textContent = getStageMode(stage) === 'rerun' ? 'Re-run' : 'Resume';
  }

  // ---------------------------------------------------------------------------
  // Collapsible panel factory — shared by Sources and Sets
  // ---------------------------------------------------------------------------

  function _makeCollapsible(bodyId, arrowId, storageKey, defaultOpen) {
    function toggle() {
      const body = document.getElementById(bodyId);
      const arrow = document.getElementById(arrowId);
      if (!body) return;
      const open = body.style.display !== 'none';
      body.style.display = open ? 'none' : '';
      if (arrow) arrow.textContent = open ? '▸' : '▾';
      try { localStorage.setItem(storageKey, open ? '0' : '1'); } catch (_) {}
    }
    function init(forceOpen) {
      const body = document.getElementById(bodyId);
      const arrow = document.getElementById(arrowId);
      if (!body) return;
      let open;
      if (forceOpen) {
        open = true;
      } else {
        try {
          const stored = localStorage.getItem(storageKey);
          open = stored !== null ? stored === '1' : defaultOpen;
        } catch (_) { open = defaultOpen; }
      }
      body.style.display = open ? '' : 'none';
      if (arrow) arrow.textContent = open ? '▾' : '▸';
    }
    return { toggle, init };
  }

  const _kb = window.KB_NAME || '';
  const _collapsibleSources = _makeCollapsible('wb-sources-body', 'wb-sources-arrow', 'kb-sources-open-' + _kb, false);
  const _collapsibleSets = _makeCollapsible('wb-sets-body', 'wb-sets-arrow', 'kb-sets-open-' + _kb, false);
  const _collapsibleKSettings = _makeCollapsible('wb-ksettings-body', 'wb-ksettings-arrow', 'kb-ksettings-open-' + _kb, false);

  function toggleSources() { _collapsibleSources.toggle(); }
  function toggleSets() { _collapsibleSets.toggle(); }
  function toggleKnowledgeSettings() { _collapsibleKSettings.toggle(); }

  function _initSources() {
    const noSources = (window.KB_SOURCES || []).length === 0;
    _collapsibleSources.init(noSources);
  }
  function _initSets() { _collapsibleSets.init(false); }
  function _initKnowledgeSettings() { _collapsibleKSettings.init(false); }

  // ---------------------------------------------------------------------------
  // Knowledge Settings toggles
  // ---------------------------------------------------------------------------

  function toggleKnowledgeCategory(kb, category, enabled) {
    fetch('/api/kb/' + kb + '/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category, enabled}),
    })
      .then(r => r.json())
      .then(() => {
        htmx.ajax('GET', '/api/kb/' + kb + '/settings/panel', {target: '#ksettings-panel', swap: 'outerHTML'});
        htmx.ajax('GET', '/pipeline/groups?kb=' + kb, {target: '#wb-stage-groups', swap: 'innerHTML'});
      })
      .catch(err => console.error('toggleKnowledgeCategory failed', err));
  }

  function toggleClassifyRule(kb, ruleId, enabled) {
    fetch('/api/kb/' + kb + '/classify-rules/' + ruleId, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled}),
    })
      .then(r => r.json())
      .then(d => { if (d.detail) console.error('toggleClassifyRule failed', d.detail); })
      .catch(err => console.error('toggleClassifyRule error', err));
  }

  // ---------------------------------------------------------------------------
  // Scope bar
  // ---------------------------------------------------------------------------

  let _liveCountTimer = null;
  let _activeSetId = null;
  let _savedScopeSnapshot = null;

  function getScope() {
    const srcSel = document.getElementById('scope-source');
    const folderSel = document.getElementById('scope-folder');
    const typeSel = document.getElementById('scope-type');
    const dateFrom = document.getElementById('scope-date-from');
    const dateTo = document.getElementById('scope-date-to');
    const nameEl = document.getElementById('scope-name-pattern');
    return {
      source_id: srcSel && srcSel.value ? parseInt(srcSel.value, 10) : null,
      folder_prefix: folderSel && folderSel.value ? folderSel.value : null,
      file_type: typeSel && typeSel.value ? typeSel.value : null,
      date_from: dateFrom && dateFrom.value ? dateFrom.value : null,
      date_to: dateTo && dateTo.value ? dateTo.value : null,
      name_pattern: nameEl && nameEl.value.trim() ? nameEl.value.trim() : null,
    };
  }

  function onScopeChange() {
    if (_activeSetId !== null && _savedScopeSnapshot !== null) {
      if (JSON.stringify(getScope()) !== _savedScopeSnapshot) {
        _activeSetId = null;
        _savedScopeSnapshot = null;
        const setSel = document.getElementById('scope-set');
        if (setSel) setSel.value = '';
      }
    }
    window.KB_SCOPE = getScope();
    _persistScope();
    _scheduleLiveCount();
    _updateDirtyIndicator();
    const srcSel = document.getElementById('scope-source');
    if (srcSel) _fetchFolders(srcSel.value ? parseInt(srcSel.value, 10) : null);
  }

  function onSetChange() {
    const setSel = document.getElementById('scope-set');
    if (!setSel) return;
    const val = setSel.value;
    if (!val) {
      _activeSetId = null;
      _savedScopeSnapshot = null;
      window.KB_SCOPE = getScope();
      _persistScope();
      _updateDirtyIndicator();
    } else {
      loadSet(parseInt(val, 10));
    }
  }

  function _persistScope() {
    try {
      const kb = window.KB_NAME || '';
      localStorage.setItem('kb-scope-' + kb, JSON.stringify(getScope()));
      if (_activeSetId !== null) {
        localStorage.setItem('kb-active-set-' + kb, String(_activeSetId));
      } else {
        localStorage.removeItem('kb-active-set-' + kb);
      }
    } catch (_) {}
  }

  function _scheduleLiveCount() {
    clearTimeout(_liveCountTimer);
    _liveCountTimer = setTimeout(() => { _fetchLiveCount(); _fetchStageCounts(); }, 300);
  }

  function _renderStageCount(sc) {
    if (!sc || sc.total === 0) return '—';
    let html = `${sc.done} / ${sc.total}`;
    if (sc.failed) html += ` <span class="count-failed">· ${sc.failed} failed</span>`;
    return html;
  }

  function _fetchStageCounts() {
    const kb = window.KB_NAME || '';
    const scope = getScope();
    const params = new URLSearchParams();
    if (scope.source_id) params.set('source_id', scope.source_id);
    if (scope.folder_prefix) params.set('folder_prefix', scope.folder_prefix);
    if (scope.file_type) params.set('file_type', scope.file_type);
    if (scope.date_from) params.set('date_from', scope.date_from);
    if (scope.date_to) params.set('date_to', scope.date_to);
    if (scope.name_pattern) params.set('name_pattern', scope.name_pattern);
    fetch('/api/kb/' + kb + '/stage-counts?' + params.toString())
      .then(r => r.json())
      .then(d => {
        Object.keys(d).forEach(stage => {
          const cell = document.getElementById('stage-files-' + stage);
          if (cell) cell.innerHTML = _renderStageCount(d[stage]);
        });
      })
      .catch(() => {});
  }

  function _fetchLiveCount() {
    const scope = getScope();
    const kb = window.KB_NAME || '';
    const params = new URLSearchParams();
    if (scope.source_id) params.set('source_id', scope.source_id);
    if (scope.folder_prefix) params.set('folder_prefix', scope.folder_prefix);
    if (scope.file_type) params.set('file_type', scope.file_type);
    if (scope.date_from) params.set('date_from', scope.date_from);
    if (scope.date_to) params.set('date_to', scope.date_to);
    if (scope.name_pattern) params.set('name_pattern', scope.name_pattern);
    const el = document.getElementById('scope-live-count');
    if (!el) return;
    fetch('/api/kb/' + kb + '/sets/preview?' + params.toString())
      .then(r => r.json())
      .then(d => { if (el) el.textContent = d.file_count; })
      .catch(() => { if (el) el.textContent = '?'; });
  }

  function _fetchFolders(sourceId) {
    const kb = window.KB_NAME || '';
    const folderSel = document.getElementById('scope-folder');
    if (!folderSel) return;
    const url = '/api/kb/' + kb + '/folders' + (sourceId ? '?source_id=' + sourceId : '');
    fetch(url)
      .then(r => r.json())
      .then(d => {
        const prev = folderSel.value;
        while (folderSel.options.length > 1) folderSel.remove(1);
        (d.folders || []).forEach(f => {
          const opt = document.createElement('option');
          opt.value = f;
          opt.textContent = f;
          folderSel.appendChild(opt);
        });
        if (prev && Array.from(folderSel.options).some(o => o.value === prev)) {
          folderSel.value = prev;
        }
      })
      .catch(() => {});
  }

  function _updateDirtyIndicator() {
    const dot = document.getElementById('scope-dirty-dot');
    if (!dot) return;
    if (_activeSetId === null || _savedScopeSnapshot === null) {
      dot.style.display = 'none';
      return;
    }
    const current = JSON.stringify(getScope());
    dot.style.display = current !== _savedScopeSnapshot ? '' : 'none';
  }

  function loadSet(setId) {
    const kb = window.KB_NAME || '';
    fetch('/api/kb/' + kb + '/sets/' + setId)
      .then(r => r.json())
      .then(d => {
        const srcSel = document.getElementById('scope-source');
        const folderSel = document.getElementById('scope-folder');
        const typeSel = document.getElementById('scope-type');
        const dateFrom = document.getElementById('scope-date-from');
        const dateTo = document.getElementById('scope-date-to');
        const nameEl = document.getElementById('scope-name-pattern');
        const setSel = document.getElementById('scope-set');
        if (srcSel) srcSel.value = d.source_id || '';
        if (typeSel) typeSel.value = d.file_type || '';
        if (dateFrom) dateFrom.value = d.date_from || '';
        if (dateTo) dateTo.value = d.date_to || '';
        if (nameEl) nameEl.value = d.name_pattern || '';
        if (setSel) setSel.value = String(setId);
        _activeSetId = setId;

        const finalize = () => {
          _savedScopeSnapshot = JSON.stringify(getScope());
          window.KB_SCOPE = getScope();
          _persistScope();
          _scheduleLiveCount();
          _updateDirtyIndicator();
        };

        if (d.source_id) {
          _fetchFolders(d.source_id);
          setTimeout(() => {
            if (folderSel && d.folder_prefix) folderSel.value = d.folder_prefix;
            finalize();
          }, 400);
        } else {
          _fetchFolders(null);
          if (folderSel && d.folder_prefix) folderSel.value = d.folder_prefix;
          finalize();
        }
      })
      .catch(err => console.error('loadSet failed', err));
  }

  function _refreshSetDropdown() {
    const kb = window.KB_NAME || '';
    const setSel = document.getElementById('scope-set');
    if (!setSel) return;
    fetch('/api/kb/' + kb + '/sets')
      .then(r => r.json())
      .then(sets => {
        while (setSel.options.length > 1) setSel.remove(1);
        sets.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = s.name;
          setSel.appendChild(opt);
        });
        if (_activeSetId !== null && Array.from(setSel.options).some(o => parseInt(o.value, 10) === _activeSetId)) {
          setSel.value = String(_activeSetId);
        } else {
          setSel.value = '';
        }
      })
      .catch(() => {});
  }

  function deleteSet(setId, kb) {
    const _k = kb || window.KB_NAME || '';
    fetch('/api/kb/' + _k + '/sets/' + setId, {method: 'DELETE'})
      .then(r => {
        if (!r.ok) return;
        if (_activeSetId !== null && parseInt(setId, 10) === _activeSetId) {
          _activeSetId = null;
          _savedScopeSnapshot = null;
          _persistScope();
          _updateDirtyIndicator();
        }
        _refreshSetDropdown();
        htmx.ajax('GET', '/api/kb/' + _k + '/sets/panel', {target: '#sets-panel', swap: 'outerHTML'});
      })
      .catch(err => console.error('deleteSet error', err));
  }

  function _initScopeBar() {
    const kb = window.KB_NAME || '';
    const sources = window.KB_SOURCES || [];
    const srcSel = document.getElementById('scope-source');
    if (srcSel) {
      sources.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.path;
        srcSel.appendChild(opt);
      });
    }

    // Restore scope + active set from localStorage
    try {
      const stored = localStorage.getItem('kb-scope-' + kb);
      if (stored) {
        const sc = JSON.parse(stored);
        if (srcSel && sc.source_id) srcSel.value = sc.source_id;
        const typeSel = document.getElementById('scope-type');
        if (typeSel && sc.file_type) typeSel.value = sc.file_type;
        const dateFrom = document.getElementById('scope-date-from');
        if (dateFrom && sc.date_from) dateFrom.value = sc.date_from;
        const dateTo = document.getElementById('scope-date-to');
        if (dateTo && sc.date_to) dateTo.value = sc.date_to;
        const nameEl = document.getElementById('scope-name-pattern');
        if (nameEl && sc.name_pattern) nameEl.value = sc.name_pattern;

        const activeSetStr = localStorage.getItem('kb-active-set-' + kb);
        if (activeSetStr) {
          _activeSetId = parseInt(activeSetStr, 10);
          _savedScopeSnapshot = stored;
        }
      }
    } catch (_) {}

    // Populate set dropdown and restore selection
    const setSel = document.getElementById('scope-set');
    if (setSel) {
      fetch('/api/kb/' + kb + '/sets')
        .then(r => r.json())
        .then(sets => {
          sets.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.name;
            setSel.appendChild(opt);
          });
          if (_activeSetId !== null && Array.from(setSel.options).some(o => parseInt(o.value, 10) === _activeSetId)) {
            setSel.value = String(_activeSetId);
          }
        })
        .catch(() => {});
    }

    // Fetch initial folders
    const srcVal = srcSel && srcSel.value ? parseInt(srcSel.value, 10) : null;
    _fetchFolders(srcVal);

    // Restore folder after folders load
    try {
      const stored = localStorage.getItem('kb-scope-' + kb);
      if (stored) {
        const sc = JSON.parse(stored);
        if (sc.folder_prefix) {
          setTimeout(() => {
            const folderSel = document.getElementById('scope-folder');
            if (folderSel) folderSel.value = sc.folder_prefix;
          }, 400);
        }
      }
    } catch (_) {}

    window.KB_SCOPE = getScope();
    _scheduleLiveCount();
    _updateDirtyIndicator();
  }

  function clearScope() {
    ['scope-set', 'scope-source', 'scope-folder', 'scope-type'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    ['scope-date-from', 'scope-date-to', 'scope-name-pattern'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    _activeSetId = null;
    _savedScopeSnapshot = null;
    window.KB_SCOPE = getScope();
    _persistScope();
    _scheduleLiveCount();
    _updateDirtyIndicator();
    _fetchFolders(null);
  }

  function promptSaveSet() {
    const form = document.getElementById('scope-save-form');
    if (!form) return;
    const sc = getScope();
    const parts = [];
    if (sc.file_type) parts.push(sc.file_type);
    if (sc.folder_prefix) parts.push(sc.folder_prefix.split('/').pop() || sc.folder_prefix);
    if (sc.date_from || sc.date_to) parts.push((sc.date_from || '') + '–' + (sc.date_to || ''));
    if (sc.name_pattern) parts.push(sc.name_pattern);
    const nameEl = document.getElementById('scope-save-name');
    if (nameEl) nameEl.value = parts.join(' ') || 'My Set';
    form.style.display = form.style.display === 'none' ? '' : 'none';
  }

  function confirmSaveSet() {
    const kb = window.KB_NAME || '';
    const name = (document.getElementById('scope-save-name') || {}).value || '';
    const result = document.getElementById('scope-save-result');
    if (!name.trim()) { if (result) result.textContent = 'Name required'; return; }
    const sc = getScope();
    const body = {name: name.trim(), description: ''};
    if (sc.source_id) body.source_id = sc.source_id;
    if (sc.folder_prefix) body.folder_prefix = sc.folder_prefix;
    if (sc.file_type) body.file_type = sc.file_type;
    if (sc.date_from) body.date_from = sc.date_from;
    if (sc.date_to) body.date_to = sc.date_to;
    if (sc.name_pattern) body.name_pattern = sc.name_pattern;
    if (result) result.textContent = 'Saving…';
    fetch('/api/kb/' + kb + '/sets', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
    .then(r => r.json())
    .then(d => {
      if (d.detail) { if (result) result.textContent = 'Error: ' + d.detail; return; }
      _activeSetId = d.id;
      _savedScopeSnapshot = JSON.stringify(getScope());
      _persistScope();
      _updateDirtyIndicator();
      if (result) result.textContent = 'Saved (' + d.file_count + ' files)';
      setTimeout(() => { const f = document.getElementById('scope-save-form'); if (f) f.style.display = 'none'; }, 1500);
      _refreshSetDropdown();
      htmx.ajax('GET', '/api/kb/' + kb + '/sets/panel', {target: '#sets-panel', swap: 'outerHTML'});
    })
    .catch(() => { if (result) result.textContent = 'Save failed'; });
  }

  function cancelSaveSet() {
    const form = document.getElementById('scope-save-form');
    if (form) form.style.display = 'none';
  }

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
    const stageSet = new Set(stages);
    const completed = (window.KB_CHECKPOINTS || []).filter(s => !stageSet.has(s));

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

  // ---------------------------------------------------------------------------
  // Sources sync (ingest stage, wired to the Sources header)
  // ---------------------------------------------------------------------------

  let _syncEs = null;

  function syncSources() {
    const kb = window.KB_NAME;
    const runBtn = document.getElementById('btn-sync-sources');
    const cancelBtn = document.getElementById('btn-cancel-sync');
    const progressEl = document.getElementById('sync-progress');

    if (runBtn) runBtn.disabled = true;
    if (cancelBtn) cancelBtn.style.display = '';
    if (progressEl) progressEl.textContent = 'Starting…';

    const scope = window.KB_SCOPE || {};
    fetch('/api/stages/ingest/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kb, run_mode: 'resume', ...scope}),
    });

    if (_syncEs) { _syncEs.close(); _syncEs = null; }
    const es = new EventSource('/api/stages/ingest/stream');
    _syncEs = es;

    es.onmessage = function(e) {
      const d = JSON.parse(e.data);
      if (d.status === 'running') {
        const msg = d.message || 'Running…';
        const count = d.total > 0 ? ` · ${d.current} / ${d.total}` : '';
        if (progressEl) progressEl.textContent = msg + count;
      } else if (d.status === 'done') {
        es.close(); _syncEs = null;
        if (!window.WB_RUNNING_PLAN) location.reload();
      } else if (d.status === 'failed') {
        es.close(); _syncEs = null;
        if (runBtn) runBtn.disabled = false;
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (progressEl) progressEl.textContent = d.message || 'Sync failed';
      }
    };
    es.onerror = function() {
      es.close(); _syncEs = null;
      if (runBtn) runBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (progressEl) progressEl.textContent = '';
    };
  }

  function cancelSync() {
    fetch('/api/stages/ingest/cancel', {method: 'POST'});
    if (_syncEs) { _syncEs.close(); _syncEs = null; }
    const runBtn = document.getElementById('btn-sync-sources');
    const cancelBtn = document.getElementById('btn-cancel-sync');
    const progressEl = document.getElementById('sync-progress');
    if (runBtn) runBtn.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (progressEl) progressEl.textContent = '';
  }

  function runSelected() { _runPlan(_checkedStages()); }
  function runGroup(groupId) { _runPlan((window.KB_GROUP_STAGES || {})[groupId] || []); }
  function runAll() {
    // Always include ingest as the first step so new sources are picked up.
    const all = ['ingest', ...(window.KB_GROUPS || []).flatMap(id => (window.KB_GROUP_STAGES || {})[id] || [])];
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
    _initSets();
    _initKnowledgeSettings();
    _initScopeBar();
  });

  return {
    runSelected, runGroup, runAll, toggleHelp, onCheckChange, onScopeChange, onSetChange, getScope,
    toggleSources, toggleSets, toggleKnowledgeSettings, setAllModes, toggleStageMode, getStageMode,
    loadSet, deleteSet, clearScope, promptSaveSet, confirmSaveSet, cancelSaveSet,
    syncSources, cancelSync,
    toggleKnowledgeCategory, toggleClassifyRule,
  };
})();
