document.addEventListener('DOMContentLoaded', function () {
  const sel = document.getElementById('kb-switcher');
  if (!sel) return;
  const current = sel.dataset.current;

  fetch('/api/kb')
    .then(r => r.json())
    .then(data => {
      sel.innerHTML = '';
      data.kbs.forEach(kb => {
        const opt = document.createElement('option');
        opt.value = kb.name;
        opt.textContent = kb.name;
        if (kb.name === current) opt.selected = true;
        sel.appendChild(opt);
      });
    });

  sel.addEventListener('change', function () {
    const params = new URLSearchParams(window.location.search);
    params.set('kb', this.value);
    window.location.href = window.location.pathname + '?' + params.toString();
  });
});
