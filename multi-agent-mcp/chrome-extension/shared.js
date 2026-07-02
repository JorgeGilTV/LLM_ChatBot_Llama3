/* OneView GocView Chrome extension — shared API client */
(function (global) {
  'use strict';

  const DEFAULT_BASE = 'https://gocview.arlocloud.com';
  const FETCH_TIMEOUT_MS = 15 * 60 * 1000;

  function normalizeBase(url) {
    const u = (url || '').trim().replace(/\/+$/, '');
    return u || DEFAULT_BASE;
  }

  async function getBaseUrl() {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.sync) {
      const stored = await chrome.storage.sync.get(['gocviewBaseUrl']);
      return normalizeBase(stored.gocviewBaseUrl);
    }
    return DEFAULT_BASE;
  }

  async function setBaseUrl(url) {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.sync) {
      await chrome.storage.sync.set({ gocviewBaseUrl: normalizeBase(url) });
    }
  }

  async function getActiveTabUrl() {
    try {
      if (typeof chrome !== 'undefined' && chrome.tabs && chrome.tabs.query) {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
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

  async function sendChatToSlack(input, sourceUrl) {
    const base = await getBaseUrl();
    const endpoint = base + '/api/extension/chat';
    const res = await fetchWithTimeout(
      endpoint,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input: (input || '').trim(),
          source_url: sourceUrl || '',
        }),
      },
      FETCH_TIMEOUT_MS
    );
    const text = await res.text();
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
    return { ok: res.ok, status: res.status, data: data };
  }

  global.GocViewExtension = {
    DEFAULT_BASE: DEFAULT_BASE,
    getBaseUrl: getBaseUrl,
    setBaseUrl: setBaseUrl,
    getActiveTabUrl: getActiveTabUrl,
    sendChatToSlack: sendChatToSlack,
  };
})(typeof window !== 'undefined' ? window : self);
