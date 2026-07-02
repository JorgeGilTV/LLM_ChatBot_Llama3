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

  const runtime = api.runtime || (typeof browser !== 'undefined' ? browser : chrome).runtime;
  const logoUrl = runtime.getURL('icons/arlo-logo.png');

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
    '    <div class="gocview-btn-row">' +
    '      <button id="gocview-quick" type="button" class="gocview-btn gocview-btn--secondary">Quick</button>' +
    '      <button id="gocview-deep" type="button" class="gocview-btn">Deep</button>' +
    '    </div>' +
    '    <p id="gocview-status" role="status"></p>' +
    '    <div id="gocview-answer" class="gocview-answer"></div>' +
    '    <p id="gocview-powered">Powered by GocBedrock · Arlo GOC</p>' +
    '  </div>' +
    '</div>';

  document.documentElement.appendChild(root);

  const fab = document.getElementById('gocview-fab');
  const panel = document.getElementById('gocview-panel');
  const panelLogo = document.getElementById('gocview-panel-logo');
  const closeBtn = document.getElementById('gocview-panel-close');
  const queryEl = document.getElementById('gocview-query');
  const quickBtn = document.getElementById('gocview-quick');
  const deepBtn = document.getElementById('gocview-deep');
  const statusEl = document.getElementById('gocview-status');
  const answerEl = document.getElementById('gocview-answer');

  panelLogo.src = logoUrl;
  fab.innerHTML = '<img src="' + logoUrl + '" alt="" style="width:28px;height:auto;filter:brightness(0) invert(1);pointer-events:none;" />';
  fab.style.display = 'flex';
  fab.style.alignItems = 'center';
  fab.style.justifyContent = 'center';

  function setStatus(msg, kind) {
    statusEl.textContent = msg || '';
    statusEl.className = kind === 'err' ? 'gocview-err' : kind === 'ok' ? 'gocview-ok' : '';
  }

  function setBusy(busy) {
    quickBtn.disabled = busy;
    deepBtn.disabled = busy;
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

  async function runChat(mode) {
    const query = (queryEl.value || '').trim();
    if (!query) {
      setStatus('Please enter a question.', 'err');
      return;
    }

    api.clearAnswer(answerEl);
    setBusy(true);
    const label = mode === 'quick' ? 'Quick Search' : 'Deep Search';
    setStatus('⏳ ' + label + ' + Slack…');

    try {
      const sourceUrl = window.location.href || '';
      const ref = await api.sendChat(query, sourceUrl, mode);
      const data = ref.data || {};
      if (ref.ok && data.success) {
        api.renderAnswer(answerEl, data);
        const secs = data.exec_time != null ? ' (' + data.exec_time + 's)' : '';
        setStatus('✅ Answer below · Slack' + secs, 'ok');
      } else {
        setStatus('❌ ' + (data.error || 'Server error'), 'err');
      }
    } catch (err) {
      const msg = err && err.name === 'AbortError'
        ? 'Request timed out'
        : (err && err.message ? err.message : 'Network error');
      setStatus('❌ ' + msg, 'err');
    } finally {
      setBusy(false);
    }
  }

  quickBtn.addEventListener('click', function () {
    runChat('quick');
  });
  deepBtn.addEventListener('click', function () {
    runChat('deep');
  });
  queryEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      runChat('deep');
    }
  });
})();
