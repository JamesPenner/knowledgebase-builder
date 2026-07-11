// Corpus Files browser (KB.AK1) — filter panel + "Use as scope" handoff to the workbench.
const CF = (() => {
  let _queue = null;
  let _filterTimer = null;

  function getFilters() {
    const el = (id) => document.getElementById(id);
    const source = el('cf-source');
    const folder = el('cf-folder');
    const type = el('cf-type');
    const dateFrom = el('cf-date-from');
    const dateTo = el('cf-date-to');
    const namePattern = el('cf-name-pattern');
    const state = el('cf-state');
    return {
      source_id: source && source.value ? source.value : null,
      folder_prefix: folder && folder.value.trim() ? folder.value.trim() : null,
      file_type: type && type.value ? type.value : null,
      date_from: dateFrom && dateFrom.value ? dateFrom.value : null,
      date_to: dateTo && dateTo.value ? dateTo.value : null,
      name_pattern: namePattern && namePattern.value.trim() ? namePattern.value.trim() : null,
      state: state && state.value ? state.value : null,
    };
  }

  function onFilterChange() {
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(function () {
      if (_queue) _queue.reload();
    }, 300);
  }

  function useAsScope() {
    const kb = window.KB_NAME || '';
    const filters = getFilters();
    const scope = {
      source_id: filters.source_id ? parseInt(filters.source_id, 10) : null,
      folder_prefix: filters.folder_prefix,
      file_type: filters.file_type,
      date_from: filters.date_from,
      date_to: filters.date_to,
      name_pattern: filters.name_pattern,
    };
    try {
      localStorage.setItem('kb-scope-' + kb, JSON.stringify(scope));
    } catch (_) {}
    window.location = '/pipeline?kb=' + encodeURIComponent(kb);
  }

  document.addEventListener('DOMContentLoaded', function () {
    _queue = ReviewQueue.init({
      queueId:    'corpus-files-queue',
      partialUrl: '/corpus-files/partials/list',
      kb:         window.KB_NAME || '',
      limit:      50,
      step:       50,
      sortBy:     'path',
      sortOrder:  'asc',
      hasSort:    true,
      getExtraParams: getFilters,
    });
  });

  return { getFilters, onFilterChange, useAsScope };
})();
