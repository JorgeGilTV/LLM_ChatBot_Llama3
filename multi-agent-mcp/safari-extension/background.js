'use strict';

const DEFAULT_INSTANCE = 'https://arlo.service-now.com';
const LOGIN_WAIT_MS = 180000;
const POLL_MS = 1500;

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

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
          reject(new Error('Timed out waiting for ServiceNow page load'));
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
    })
    .catch(function () {
      return '';
    });
}

function findInstanceTabs(host) {
  return chrome.tabs
    .query({ url: ['https://*.service-now.com/*', 'http://*.service-now.com/*'] })
    .then(function (tabs) {
      return (tabs || []).filter(function (tab) {
        if (!tab.url) return false;
        try {
          return new URL(tab.url).hostname === host;
        } catch (e) {
          return tab.url.indexOf(host) >= 0;
        }
      });
    });
}

function tryCaptureFromTab(tabId, host) {
  return readSnowCookies(host).then(function (cookies) {
    if (!hasSessionCookies(cookies)) {
      return null;
    }
    return readGck(tabId).then(function (g_ck) {
      if (!g_ck) {
        return null;
      }
      return {
        success: true,
        cookies: cookies,
        user_token: g_ck,
      };
    });
  });
}

function waitForLoginAndCapture(host, tabId, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || LOGIN_WAIT_MS);
  return new Promise(function (resolve, reject) {
    function poll() {
      tryCaptureFromTab(tabId, host).then(function (result) {
        if (result && result.success) {
          resolve(result);
          return;
        }
        if (Date.now() > deadline) {
          reject(
            new Error(
              'Timed out waiting for ServiceNow login. Complete Okta sign-in in the ServiceNow tab, then click Connect again.'
            )
          );
          return;
        }
        setTimeout(poll, POLL_MS);
      });
    }
    poll();
  });
}

function captureSnowSession(instance) {
  const base = (instance || DEFAULT_INSTANCE).replace(/\/+$/, '');
  const host = new URL(base).hostname;
  let createdTabId = null;

  return readSnowCookies(host)
    .then(function (cookies) {
      return findInstanceTabs(host).then(function (tabs) {
        var tabPromises = tabs.map(function (tab) {
          return tryCaptureFromTab(tab.id, host);
        });
        return Promise.all(tabPromises).then(function (results) {
          for (var i = 0; i < results.length; i++) {
            if (results[i] && results[i].success) {
              return results[i];
            }
          }
          if (hasSessionCookies(cookies)) {
            var useTab = tabs.length ? tabs[0] : null;
            if (useTab) {
              return waitForLoginAndCapture(host, useTab.id, 15000).catch(function () {
                return null;
              });
            }
            return chrome.tabs
              .create({ url: base + '/navpage.do', active: false })
              .then(function (tab) {
                createdTabId = tab.id;
                return waitTabComplete(tab.id).then(function () {
                  return waitForLoginAndCapture(host, tab.id, 20000);
                });
              })
              .finally(function () {
                if (createdTabId != null) {
                  chrome.tabs.remove(createdTabId).catch(function () {});
                  createdTabId = null;
                }
              });
          }
          return null;
        });
      });
    })
    .then(function (existing) {
      if (existing && existing.success) {
        return existing;
      }
      return chrome.tabs
        .create({ url: base + '/navpage.do', active: true })
        .then(function (tab) {
          createdTabId = tab.id;
          return waitTabComplete(tab.id).then(function () {
            return waitForLoginAndCapture(host, tab.id, LOGIN_WAIT_MS);
          });
        });
    })
    .finally(function () {
      if (createdTabId != null) {
        chrome.tabs.remove(createdTabId).catch(function () {});
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
