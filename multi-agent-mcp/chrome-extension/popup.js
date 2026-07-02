document.addEventListener('DOMContentLoaded', async function () {
  const baseUrlEl = document.getElementById('baseUrl');
  const queryEl = document.getElementById('query');
  const sendBtn = document.getElementById('sendBtn');
  const statusEl = document.getElementById('status');

  const api = window.GocViewExtension;
  if (!api) {
    statusEl.textContent = 'Error: shared.js no cargó';
    return;
  }

  baseUrlEl.value = await api.getBaseUrl();

  baseUrlEl.addEventListener('change', function () {
    api.setBaseUrl(baseUrlEl.value);
  });

  function setStatus(msg, isError) {
    statusEl.textContent = msg || '';
    statusEl.className = 'gv-status' + (isError ? ' gv-status--error' : ' gv-status--ok');
  }

  async function runChat() {
    const query = (queryEl.value || '').trim();
    if (!query) {
      setStatus('Escribe una pregunta.', true);
      return;
    }

    await api.setBaseUrl(baseUrlEl.value);
    sendBtn.disabled = true;
    setStatus('⏳ GocBedrock analizando y enviando a Slack (1–3 min)…');

    try {
      const sourceUrl = await api.getActiveTabUrl();
      const ref = await api.sendChatToSlack(query, sourceUrl);
      const data = ref.data || {};
      if (ref.ok && data.success) {
        const secs = data.exec_time != null ? ' (' + data.exec_time + 's)' : '';
        setStatus('✅ Enviado a Slack' + secs, false);
        queryEl.value = '';
      } else {
        setStatus('❌ ' + (data.error || 'Error del servidor'), true);
      }
    } catch (err) {
      const msg = err && err.name === 'AbortError'
        ? 'Tiempo de espera agotado'
        : (err && err.message ? err.message : 'Error de red');
      setStatus('❌ ' + msg, true);
    } finally {
      sendBtn.disabled = false;
    }
  }

  sendBtn.addEventListener('click', runChat);
  queryEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      runChat();
    }
  });
});
