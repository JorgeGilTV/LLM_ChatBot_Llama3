(function () {
  'use strict';

  if (window.__gocviewWidgetLoaded) {
    return;
  }
  window.__gocviewWidgetLoaded = true;

  const api = window.GocViewExtension;
  if (!api) {
    return;
  }

  const root = document.createElement('div');
  root.id = 'gocview-widget-root';
  root.innerHTML =
    '<button id="gocview-fab" type="button" title="OneView GocView Chat" aria-label="Open GocView chat">🧠</button>' +
    '<div id="gocview-panel" role="dialog" aria-label="GocView chat">' +
    '  <div id="gocview-panel-header">' +
    '    <span>OneView Chat → Slack</span>' +
    '    <button id="gocview-panel-close" type="button" aria-label="Close">×</button>' +
    '  </div>' +
    '  <div id="gocview-panel-body">' +
    '    <textarea id="gocview-query" placeholder="¿Qué está pasando con…?"></textarea>' +
    '    <button id="gocview-send" type="button">Enviar a Slack</button>' +
    '    <p id="gocview-status" role="status"></p>' +
    '  </div>' +
    '</div>';

  document.documentElement.appendChild(root);

  const fab = document.getElementById('gocview-fab');
  const panel = document.getElementById('gocview-panel');
  const closeBtn = document.getElementById('gocview-panel-close');
  const queryEl = document.getElementById('gocview-query');
  const sendBtn = document.getElementById('gocview-send');
  const statusEl = document.getElementById('gocview-status');

  function setStatus(msg, kind) {
    statusEl.textContent = msg || '';
    statusEl.className = kind === 'err' ? 'gocview-err' : kind === 'ok' ? 'gocview-ok' : '';
  }

  fab.addEventListener('click', function () {
    panel.classList.toggle('gocview-open');
    if (panel.classList.contains('gocview-open')) {
      queryEl.focus();
    }
  });

  closeBtn.addEventListener('click', function () {
    panel.classList.remove('gocview-open');
  });

  async function runChat() {
    const query = (queryEl.value || '').trim();
    if (!query) {
      setStatus('Escribe una pregunta.', 'err');
      return;
    }

    sendBtn.disabled = true;
    setStatus('⏳ Analizando y enviando a Slack (1–3 min)…');

    try {
      const sourceUrl = window.location.href || '';
      const ref = await api.sendChatToSlack(query, sourceUrl);
      const data = ref.data || {};
      if (ref.ok && data.success) {
        const secs = data.exec_time != null ? ' (' + data.exec_time + 's)' : '';
        setStatus('✅ Enviado a Slack' + secs, 'ok');
        queryEl.value = '';
      } else {
        setStatus('❌ ' + (data.error || 'Error del servidor'), 'err');
      }
    } catch (err) {
      const msg = err && err.name === 'AbortError'
        ? 'Tiempo de espera agotado'
        : (err && err.message ? err.message : 'Error de red');
      setStatus('❌ ' + msg, 'err');
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
})();
