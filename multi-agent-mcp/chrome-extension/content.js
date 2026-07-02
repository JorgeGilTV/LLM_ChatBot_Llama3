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

  const logoUrl = chrome.runtime.getURL('icons/arlo-logo.png');

  const root = document.createElement('div');
  root.id = 'gocview-widget-root';
  root.innerHTML =
    '<button id="gocview-fab" type="button" title="GocView Chatbot" aria-label="Open GocView Chatbot"></button>' +
    '<div id="gocview-panel" role="dialog" aria-label="GocView Chatbot">' +
    '  <div id="gocview-panel-header">' +
    '    <div id="gocview-panel-brand">' +
    '      <img id="gocview-panel-logo" src="" alt="Arlo" />' +
    '      <span id="gocview-panel-title">GocView Chatbot</span>' +
    '    </div>' +
    '    <button id="gocview-panel-close" type="button" aria-label="Close">×</button>' +
    '  </div>' +
    '  <div id="gocview-panel-body">' +
    '    <label id="gocview-query-label" for="gocview-query">Your question</label>' +
    '    <textarea id="gocview-query" placeholder="What is going on with…?"></textarea>' +
    '    <button id="gocview-send" type="button">Send to Slack</button>' +
    '    <p id="gocview-status" role="status"></p>' +
    '    <p id="gocview-powered">Powered by GocBedrock · Arlo GOC</p>' +
    '  </div>' +
    '</div>';

  document.documentElement.appendChild(root);

  const fab = document.getElementById('gocview-fab');
  const panel = document.getElementById('gocview-panel');
  const panelLogo = document.getElementById('gocview-panel-logo');
  const closeBtn = document.getElementById('gocview-panel-close');
  const queryEl = document.getElementById('gocview-query');
  const sendBtn = document.getElementById('gocview-send');
  const statusEl = document.getElementById('gocview-status');

  panelLogo.src = logoUrl;
  fab.style.backgroundImage = 'url("' + logoUrl + '")';
  fab.style.backgroundSize = '26px auto';
  fab.style.filter = 'none';
  /* White logo on navy FAB via CSS mask alternative: use inner img */
  fab.innerHTML = '<img src="' + logoUrl + '" alt="" style="width:28px;height:auto;filter:brightness(0) invert(1);pointer-events:none;" />';
  fab.style.backgroundImage = 'none';
  fab.style.display = 'flex';
  fab.style.alignItems = 'center';
  fab.style.justifyContent = 'center';

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
      setStatus('Please enter a question.', 'err');
      return;
    }

    sendBtn.disabled = true;
    setStatus('⏳ Analyzing and sending to Slack (1–3 min)…');

    try {
      const sourceUrl = window.location.href || '';
      const ref = await api.sendChatToSlack(query, sourceUrl);
      const data = ref.data || {};
      if (ref.ok && data.success) {
        const secs = data.exec_time != null ? ' (' + data.exec_time + 's)' : '';
        setStatus('✅ Sent to Slack' + secs, 'ok');
        queryEl.value = '';
      } else {
        setStatus('❌ ' + (data.error || 'Server error'), 'err');
      }
    } catch (err) {
      const msg = err && err.name === 'AbortError'
        ? 'Request timed out'
        : (err && err.message ? err.message : 'Network error');
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
