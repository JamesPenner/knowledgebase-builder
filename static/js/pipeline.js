const _sources = {};

function _buildBody(stage, kb) {
  if (stage === 'suggest') return JSON.stringify({kb, levels: ['a', 'b']});
  return JSON.stringify({kb});
}

function runStage(stage, kb) {
  document.getElementById('btn-run-' + stage).disabled = true;
  document.getElementById('btn-cancel-' + stage).style.display = '';
  const badge = document.getElementById('badge-' + stage);
  badge.className = 'badge badge-running';
  badge.textContent = 'running';

  fetch('/api/stages/' + stage + '/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: _buildBody(stage, kb),
  });

  const es = new EventSource('/api/stages/' + stage + '/stream');
  _sources[stage] = es;

  es.onmessage = function (e) {
    const d = JSON.parse(e.data);
    const prog = document.getElementById('progress-' + stage);
    if (d.total > 0) {
      prog.textContent = d.current + ' / ' + d.total + (d.eta > 0 ? ' · ' + d.eta + 's' : '');
    }
    if (d.status === 'done') {
      badge.className = 'badge badge-done';
      badge.textContent = 'done';
      document.getElementById('btn-run-' + stage).disabled = false;
      document.getElementById('btn-cancel-' + stage).style.display = 'none';
      prog.textContent = '';
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
  document.getElementById('progress-' + stage).textContent = '';
}
