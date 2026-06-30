/* review.js — shared review page infrastructure */

// ---------------------------------------------------------------------------
// ReviewTabs — two-tab pending / decided switcher
// ---------------------------------------------------------------------------
const ReviewTabs = (() => {
  let _pendingPanelId, _decidedPanelId, _pendingBadgeId, _storageKey;

  function _setActive(which) {
    const pendingPanel = document.getElementById(_pendingPanelId);
    const decidedPanel = document.getElementById(_decidedPanelId);
    if (pendingPanel) pendingPanel.classList.toggle('review-tab-panel--hidden', which === 'decided');
    if (decidedPanel) decidedPanel.classList.toggle('review-tab-panel--hidden', which === 'pending');
    document.querySelectorAll('.review-tab').forEach(function (tab) {
      const active = (which === 'pending' && tab.dataset.panel === _pendingPanelId) ||
                     (which === 'decided' && tab.dataset.panel === _decidedPanelId);
      tab.classList.toggle('review-tab--active', active);
    });
    try { localStorage.setItem(_storageKey, which); } catch (_) {}
  }

  function adjustPendingCount(delta) {
    const badge = document.getElementById(_pendingBadgeId);
    if (badge) badge.textContent = Math.max(0, (parseInt(badge.textContent, 10) || 0) + delta);
  }

  function init(opts) {
    _pendingPanelId = opts.pendingPanelId;
    _decidedPanelId = opts.decidedPanelId;
    _pendingBadgeId = opts.pendingBadgeId;
    _storageKey     = opts.storageKey;

    // Restore tab from localStorage
    let saved;
    try { saved = localStorage.getItem(_storageKey); } catch (_) {}
    if (saved === 'decided') _setActive('decided');

    // Wire tab clicks
    document.querySelectorAll('.review-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        _setActive(this.dataset.panel === _decidedPanelId ? 'decided' : 'pending');
      });
    });

    // Sync pending badge after HTMX refreshes the pending panel
    document.addEventListener('htmx:afterSwap', function (e) {
      const pendingPanel = document.getElementById(_pendingPanelId);
      if (!pendingPanel || !pendingPanel.contains(e.detail.target)) return;
      const countEl = e.detail.target.querySelector('[data-pending-count]');
      if (!countEl) return;
      const badge = document.getElementById(_pendingBadgeId);
      if (badge) badge.textContent = countEl.dataset.pendingCount;
    });
  }

  return { init, adjustPendingCount };
})();


// ---------------------------------------------------------------------------
// ReviewPage — optimistic row removal + debounced decisions refresh
// ---------------------------------------------------------------------------
const ReviewPage = (() => {
  function init(opts) {
    const { formClass, rowIdPrefix, idField, endpoint, onRowRemoved } = opts;
    let _decisionsTimer = null;

    function _scheduleDecisionsRefresh() {
      clearTimeout(_decisionsTimer);
      _decisionsTimer = setTimeout(function () {
        htmx.trigger(document.body, 'decisionsChanged');
      }, 1500);
    }

    function _removeRow(itemId) {
      const row = document.getElementById(rowIdPrefix + itemId);
      if (!row) return;
      if (onRowRemoved) {
        onRowRemoved(row);
      } else {
        row.remove();
      }
      if (document.querySelectorAll('.token-row').length === 0) {
        htmx.trigger(document.body, 'pendingChanged');
      }
    }

    document.addEventListener('submit', function (e) {
      const form = e.target;
      if (!form.classList.contains(formClass)) return;
      e.preventDefault();
      if (!form.reportValidity()) return;

      const data = new FormData(form);
      const itemId = data.get(idField);
      _removeRow(itemId);
      ReviewTabs.adjustPendingCount(-1);

      const kb = form.dataset.kb;
      fetch(endpoint + '?kb=' + encodeURIComponent(kb), {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(data).toString(),
      }).catch(function (err) { console.error('decide failed', err); });

      _scheduleDecisionsRefresh();
    });
  }

  return { init };
})();


// ---------------------------------------------------------------------------
// ReviewQueue — server-side sort, pagination, and Load More for pending queues
// ---------------------------------------------------------------------------
const ReviewQueue = (() => {
  function init(opts) {
    // opts: queueId, partialUrl, kb, limit, step, sortBy, sortOrder, hasSort
    const queueId   = opts.queueId;
    const partialUrl = opts.partialUrl;
    const kb        = opts.kb;
    const step      = opts.step || opts.limit || 50;
    const hasSort   = opts.hasSort !== false;

    const state = {
      limit:     opts.limit     || 50,
      sortBy:    opts.sortBy    || 'file_count',
      sortOrder: opts.sortOrder || 'desc',
    };

    function _url() {
      return partialUrl + '?kb=' + encodeURIComponent(kb)
        + '&limit='      + state.limit
        + '&sort_by='    + encodeURIComponent(state.sortBy)
        + '&sort_order=' + encodeURIComponent(state.sortOrder);
    }

    function _reload() {
      const el = document.getElementById(queueId);
      if (el) {
        el.setAttribute('hx-get', _url());
        htmx.process(el);
      }
      htmx.ajax('GET', _url(), { target: '#' + queueId, swap: 'innerHTML' });
    }

    function _updateSortIndicators() {
      if (!hasSort) return;
      const el = document.getElementById(queueId);
      if (!el) return;
      el.querySelectorAll('th[data-sort-by]').forEach(function (th) {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sortBy === state.sortBy) {
          th.classList.add(state.sortOrder === 'asc' ? 'sort-asc' : 'sort-desc');
        }
      });
    }

    // Sort header clicks
    document.addEventListener('click', function (e) {
      if (!hasSort) return;
      const th = e.target.closest('th[data-sort-by]');
      if (!th) return;
      const el = document.getElementById(queueId);
      if (!el || !el.contains(th)) return;
      const col = th.dataset.sortBy;
      if (state.sortBy === col) {
        state.sortOrder = state.sortOrder === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortBy = col;
        state.sortOrder = 'desc';
      }
      state.limit = step;
      _reload();
    });

    // Load More button
    document.addEventListener('click', function (e) {
      const btn = e.target.closest('[data-load-more]');
      if (!btn) return;
      const el = document.getElementById(queueId);
      if (!el || !el.contains(btn)) return;
      state.limit += step;
      _reload();
    });

    // Records-per-page selector
    document.addEventListener('change', function (e) {
      const sel = e.target.closest('[data-page-size]');
      if (!sel) return;
      const el = document.getElementById(queueId);
      if (!el || !el.contains(sel)) return;
      const val = sel.value;
      state.limit = parseInt(val, 10);
      _reload();
    });

    // Re-apply sort indicators after every HTMX swap of this queue
    document.addEventListener('htmx:afterSwap', function (e) {
      if (e.detail.target && e.detail.target.id === queueId) {
        _updateSortIndicators();
      }
    });

    document.addEventListener('DOMContentLoaded', function () {
      _updateSortIndicators();
    });
  }

  return { init };
})();


// ---------------------------------------------------------------------------
// ReviewFilter — text search, action filter, and column sort for decisions
// ---------------------------------------------------------------------------
const ReviewFilter = (() => {
  function init(opts) {
    const { tableId, searchId, filterBtns } = opts;
    let filterText   = '';
    let filterAction = '';
    let sortCol      = null;
    let sortDir      = 1;

    function _applyFilter() {
      const container = document.getElementById(tableId);
      if (!container) return;
      container.querySelectorAll('.decision-row').forEach(function (row) {
        const token  = (row.querySelector('.decision-token')  || {}).textContent || '';
        const detail = (row.querySelector('.decision-detail') || {}).textContent || '';
        const action = row.dataset.action || '';
        const matchText   = !filterText   || token.toLowerCase().indexOf(filterText)   !== -1 ||
                                             detail.toLowerCase().indexOf(filterText)  !== -1;
        const matchAction = !filterAction || action === filterAction;
        row.style.display = (matchText && matchAction) ? '' : 'none';
      });
    }

    function _sortTable() {
      if (!sortCol) return;
      const container = document.getElementById(tableId);
      if (!container) return;
      const tbody = container.querySelector('.decisions-table tbody');
      if (!tbody) return;
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort(function (a, b) {
        const aVal = sortCol === 'action'
          ? ((a.querySelector('.badge') || {}).textContent || '').trim()
          : ((a.querySelector('.decision-' + sortCol) || {}).textContent || '').trim();
        const bVal = sortCol === 'action'
          ? ((b.querySelector('.badge') || {}).textContent || '').trim()
          : ((b.querySelector('.decision-' + sortCol) || {}).textContent || '').trim();
        return sortDir * aVal.localeCompare(bVal);
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
    }

    function _updateSortIndicators() {
      const container = document.getElementById(tableId);
      if (!container) return;
      container.querySelectorAll('.decisions-table th[data-sort]').forEach(function (th) {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === sortCol) th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
      });
    }

    function _initSortHeaders() {
      const container = document.getElementById(tableId);
      if (!container) return;
      container.querySelectorAll('.decisions-table th[data-sort]').forEach(function (th) {
        th.addEventListener('click', function () {
          const col = this.dataset.sort;
          if (sortCol === col) { sortDir *= -1; } else { sortCol = col; sortDir = 1; }
          _sortTable();
          _updateSortIndicators();
          _applyFilter();
        });
      });
      _updateSortIndicators();
    }

    document.addEventListener('htmx:afterSwap', function (e) {
      if (e.detail && e.detail.target && e.detail.target.id === tableId) {
        _initSortHeaders();
        _sortTable();
        _updateSortIndicators();
        _applyFilter();
      }
    });

    document.addEventListener('DOMContentLoaded', function () {
      const search = document.getElementById(searchId);
      if (search) {
        search.addEventListener('input', function () {
          filterText = this.value.toLowerCase();
          _applyFilter();
        });
      }
      if (filterBtns) {
        document.querySelectorAll(filterBtns).forEach(function (btn) {
          btn.addEventListener('click', function () {
            document.querySelectorAll(filterBtns).forEach(function (b) { b.classList.remove('active'); });
            this.classList.add('active');
            filterAction = this.dataset.action || '';
            _applyFilter();
          });
        });
      }
      _initSortHeaders();
    });
  }

  return { init };
})();
