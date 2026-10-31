// Keep the form usable without JavaScript while updating results in place when available.
(() => {
  const form = document.getElementById('search-form');
  const results = document.getElementById('search-results');
  if (!form || !results) return;
  let searched = form.dataset.searched === 'true';
  let controller;
  let timer;

  async function updateResults() {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = controller;
    const params = new URLSearchParams(new FormData(form));
    const url = `${form.action}?${params}`;
    results.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, {signal: current.signal, headers: {'Accept': 'text/html'}});
      if (!response.ok && response.status !== 400) throw new Error('Search failed');
      const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
      const newResults = documentCopy.getElementById('search-results');
      if (!newResults) throw new Error('Search response was incomplete');
      if (current !== controller) return;
      results.innerHTML = newResults.innerHTML;
      for (const input of form.querySelectorAll('input, select')) {
        const error = document.getElementById(`${input.name}-error`);
        const updatedError = documentCopy.getElementById(`${input.name}-error`);
        if (!error || !updatedError) continue;
        error.textContent = updatedError.textContent;
        error.hidden = updatedError.hidden;
        if (error.hidden) {
          input.removeAttribute('aria-invalid');
          input.removeAttribute('aria-describedby');
        } else {
          input.setAttribute('aria-invalid', 'true');
          input.setAttribute('aria-describedby', error.id);
        }
      }
      history.replaceState(null, '', `${location.pathname}?${params}`);
    } catch (error) {
      if (error.name !== 'AbortError') results.textContent = 'Unable to update results. Please try searching again.';
    } finally {
      if (current === controller) results.removeAttribute('aria-busy');
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    clearTimeout(timer);
    searched = true;
    updateResults();
  });
  form.addEventListener('input', () => {
    if (!searched) return;
    clearTimeout(timer);
    if (controller) controller.abort();
    if (!form.checkValidity()) {
      results.removeAttribute('aria-busy');
      results.textContent = 'Enter valid search details to update the results.';
      return;
    }
    timer = setTimeout(updateResults, 300);
  });
})();
