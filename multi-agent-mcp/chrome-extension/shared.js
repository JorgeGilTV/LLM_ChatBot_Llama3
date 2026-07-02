/* GocView Chatbot — shared API client (Chrome + Firefox + Edge + Safari) */
(function (global) {
  'use strict';

  const ext = typeof browser !== 'undefined' ? browser : chrome;
  const DEFAULT_BASE = 'https://gocview.arlocloud.com';
  const FETCH_TIMEOUT_MS = 15 * 60 * 1000;
  const QUICK_TIMEOUT_MS = 5 * 60 * 1000;

  function normalizeBase(url) {
    const u = (url || '').trim().replace(/\/+$/, '');
    return u || DEFAULT_BASE;
  }

  async function getBaseUrl() {
    if (ext.storage && ext.storage.sync) {
      const stored = await ext.storage.sync.get(['gocviewBaseUrl']);
      return normalizeBase(stored.gocviewBaseUrl);
    }
    return DEFAULT_BASE;
  }

  async function setBaseUrl(url) {
    if (ext.storage && ext.storage.sync) {
      await ext.storage.sync.set({ gocviewBaseUrl: normalizeBase(url) });
    }
  }

  async function getActiveTabUrl() {
    try {
      if (ext.tabs && ext.tabs.query) {
        const tabs = await ext.tabs.query({ active: true, currentWindow: true });
        if (tabs && tabs[0] && tabs[0].url) {
          return tabs[0].url;
        }
      }
    } catch (_e) {
      /* ignore */
    }
    return '';
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    const ms = timeoutMs || FETCH_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(function () {
      controller.abort();
    }, ms);
    return fetch(url, Object.assign({}, options || {}, { signal: controller.signal }))
      .finally(function () {
        clearTimeout(timer);
      });
  }

  function parseResponse(res, text) {
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_e) {
      if (res.status === 404) {
        data = {
          success: false,
          error:
            'Endpoint /api/extension/chat is not available on this server. ' +
            'Deploy the latest GocView build or use http://127.0.0.1:8080 for local dev.',
        };
      } else {
        data = { success: false, error: (text || 'Invalid server response').slice(0, 280) };
      }
    }
    return data;
  }

  async function sendChat(input, sourceUrl, mode) {
    const chatMode = mode === 'quick' ? 'quick' : 'deep';
    const base = await getBaseUrl();
    const endpoint = base + '/api/extension/chat';
    const timeout = chatMode === 'quick' ? QUICK_TIMEOUT_MS : FETCH_TIMEOUT_MS;
    const res = await fetchWithTimeout(
      endpoint,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input: (input || '').trim(),
          source_url: sourceUrl || '',
          mode: chatMode,
        }),
      },
      timeout
    );
    const text = await res.text();
    const data = parseResponse(res, text);
    return { ok: res.ok, status: res.status, data: data };
  }

  /** @deprecated use sendChat */
  async function sendChatToSlack(input, sourceUrl) {
    return sendChat(input, sourceUrl, 'deep');
  }

  function renderAnswer(targetEl, data) {
    if (!targetEl) {
      return;
    }
    const html = data && data.answer_html;
    const text = data && data.answer_text;
    targetEl.classList.add('gv-answer--visible');
    if (html && /<[a-z][\s\S]*>/i.test(String(html))) {
      targetEl.innerHTML = String(html);
    } else if (text) {
      targetEl.textContent = String(text);
    } else {
      targetEl.textContent = 'No answer content returned.';
    }
  }

  function clearAnswer(targetEl) {
    if (!targetEl) {
      return;
    }
    targetEl.classList.remove('gv-answer--visible');
    targetEl.innerHTML = '';
  }

  global.GocViewExtension = {
    DEFAULT_BASE: DEFAULT_BASE,
    getBaseUrl: getBaseUrl,
    setBaseUrl: setBaseUrl,
    getActiveTabUrl: getActiveTabUrl,
    sendChat: sendChat,
    sendChatToSlack: sendChatToSlack,
    renderAnswer: renderAnswer,
    clearAnswer: clearAnswer,
    runtime: ext.runtime,
  };
})(typeof window !== 'undefined' ? window : self);
