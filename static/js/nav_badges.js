document.addEventListener('DOMContentLoaded', () => {
  const kb = document.getElementById('kb-switcher')?.dataset.kb;
  if (!kb) return;
  [
    { id: 'normalise-badge', url: '/api/review/normalise/pending' },
    { id: 'suggest-badge',   url: '/api/review/suggest/pending'   },
    { id: 'new-terms-badge', url: '/api/review/new-terms/pending' },
  ].forEach(({ id, url }) => {
    const badge = document.getElementById(id);
    if (!badge) return;
    fetch(`${url}?kb=${encodeURIComponent(kb)}&limit=0`)
      .then(r => r.json())
      .then(data => {
        const n = data.counts?.pending ?? 0;
        if (n > 0) { badge.textContent = n; badge.style.display = 'inline'; }
      })
      .catch(() => {});
  });
});
