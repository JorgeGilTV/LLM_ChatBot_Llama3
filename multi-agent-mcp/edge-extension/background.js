'use strict';

const DEFAULT_INSTANCE = 'https://arlo.service-now.com';

function waitTabComplete(tabId, timeoutMs) {
  const limit = timeoutMs || 120000;
  return new Promise(function (resolve, reject) {
    const deadline = Date.now() + limit;
    function check() {
      chrome.tabs.get(tabId, function (tab) {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (tab && tab.status === 'complete') {
          resolve(tab);
          return;
        }
        if (Date.now() > deadline) {
          reject(new Error('Tiempo agotado esperando ServiceNow'));
          return;
        }
        setTimeout(check, 400);
      });
    }
    check();
  });
}

function readSnowCookies(host) {
  const domains = [host, '.service-now.com'];
  return Promise.all(
    domains.map(function (domain) {
      return chrome.cookies.getAll({ domain: domain });
    })
  ).then(function (groups) {
    const cookieMap = {};
    groups.forEach(function (all) {
      (all || []).forEach(function (c) {
        if (c.name && c.value) {
          cookieMap[c.name] = c.value;
        }
      });
    });
    return cookieMap;
  });
}

function hasSessionCookies(cookies) {
  const keys = Object.keys(cookies || {}).map(function (k) {
    return k.toLowerCase();
  });
  return keys.indexOf('glide_session_store') >= 0 || keys.indexOf('jsessionid') >= 0;
}

function readGck(tabId) {
  return chrome.scripting
    .executeScript({
      target: { tabId: tabId },
      func: function () {
        if (typeof window.g_ck === 'string' && window.g_ck.length > 5) {
          return window.g_ck;
        }
        if (window.NOW && typeof window.NOW.g_ck === 'string') {
          return window.NOW.g_ck;
        }
        var meta = document.querySelector('meta[name="g_ck"]');
        if (meta && meta.content) {
          return meta.content;
        }
        return '';
      },
    })
    .then(function (results) {
      return (results && results[0] && results[0].result) || '';
    });
}

function captureSnowSession(instance) {
  const base = (instance || DEFAULT_INSTANCE).replace(/\/+$/, '');
  const host = new URL(base).hostname;
  let tabId = null;
  let created = false;

  return readSnowCookies(host)
    .then(function (cookies) {
      const needsLogin = !hasSessionCookies(cookies);
      return chrome.tabs
        .create({
          url: base + '/navpage.do',
          active: needsLogin,
        })
        .then(function (tab) {
          tabId = tab.id;
          created = true;
          return waitTabComplete(tabId);
        })
        .then(function () {
          return readSnowCookies(host);
        })
        .then(function (fresh) {
          cookies = fresh;
          if (!hasSessionCookies(cookies)) {
            throw new Error(
              'No hay sesión ServiceNow. Inicia sesión con Okta en la pestaña que se abrió e intenta de nuevo.'
            );
          }
          return readGck(tabId).then(function (g_ck) {
            if (!g_ck) {
              throw new Error('No se obtuvo g_ck tras abrir ServiceNow.');
            }
            return readSnowCookies(host).then(function (finalCookies) {
              return {
                success: true,
                cookies: finalCookies,
                user_token: g_ck,
              };
            });
          });
        });
    })
    .finally(function () {
      if (created && tabId != null) {
        chrome.tabs.remove(tabId).catch(function () {});
      }
    });
}

chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (msg && msg.type === 'GOCVIEW_CAPTURE_SNOW') {
    captureSnowSession(msg.instance)
      .then(function (result) {
        sendResponse(result);
      })
      .catch(function (err) {
        sendResponse({
          success: false,
          error: err && err.message ? err.message : String(err),
        });
      });
    return true;
  }
  return false;
});
