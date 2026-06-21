document.addEventListener('DOMContentLoaded', () => {
  const badge = document.getElementById('suggest-badge');
  if (!badge) return;
  const kb = badge.dataset.kb;
  if (!kb) return;
  fetch(`/api/review/suggest/pending?kb=${encodeURIComponent(kb)}&limit=0`)
    .then(r => r.json())
    .then(data => {
      const count = data.counts?.pending ?? 0;
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'inline';
      }
    })
    .catch(() => {});
});
