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

function extractGckFromPage() {
  try {
    if (typeof window.g_ck === 'string' && window.g_ck.length > 5) {
      return window.g_ck;
    }
    if (window.NOW && typeof window.NOW.g_ck === 'string' && window.NOW.g_ck.length > 5) {
      return window.NOW.g_ck;
    }
    if (window.NOW && window.NOW.user && typeof window.NOW.user.g_ck === 'string') {
      return window.NOW.user.g_ck;
    }
    var meta = document.querySelector('meta[name="g_ck"]');
    if (meta && meta.content) {
      return meta.content;
    }
    if (window.GlideSession && typeof window.GlideSession.getSessionToken === 'function') {
      var token = window.GlideSession.getSessionToken();
      if (token) {
        return token;
      }
    }
    var scripts = document.getElementsByTagName('script');
    for (var i = 0; i < scripts.length; i++) {
      var txt = scripts[i].textContent || '';
      var m = txt.match(/g_ck\s*[:=]\s*['"]([a-zA-Z0-9_-]{16,})['"]/);
      if (m) {
        return m[1];
      }
    }
  } catch (e) {
    /* ignore */
  }
  return '';
}

function readGck(tabId) {
  return chrome.scripting
    .executeScript({
      target: { tabId: tabId },
      func: extractGckFromPage,
    })
    .then(function (results) {
      return (results && results[0] && results[0].result) || '';
    })
    .catch(function () {
      return '';
    });
}

function classicNavUrl(base) {
  return base.replace(/\/+$/, '') + '/navpage.do';
}

function isClassicSnUrl(url) {
  if (!url) {
    return false;
  }
  return /navpage\.do|\/classic\//i.test(url);
}

function ensureClassicPage(tabId, base) {
  return chrome.tabs.get(tabId).then(function (tab) {
    if (tab && isClassicSnUrl(tab.url)) {
      return tab;
    }
    return chrome.tabs
      .update(tabId, { url: classicNavUrl(base) })
      .then(function () {
        return waitTabComplete(tabId, 90000);
      })
      .then(function () {
        return sleep(2000);
      });
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

function tryCaptureFromTab(tabId, host, base, options) {
  const opts = options || {};
  return readSnowCookies(host).then(function (cookies) {
    if (!hasSessionCookies(cookies)) {
      return null;
    }
    function readToken() {
      return readGck(tabId).then(function (g_ck) {
        if (g_ck) {
          return {
            success: true,
            cookies: cookies,
            user_token: g_ck,
          };
        }
        return null;
      });
    }
    return readToken().then(function (result) {
      if (result || opts.skipNav) {
        return result;
      }
      return ensureClassicPage(tabId, base)
        .then(function () {
          return readToken();
        })
        .catch(function () {
          return null;
        });
    });
  });
}

function waitForLoginAndCapture(host, tabId, base, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || LOGIN_WAIT_MS);
  let navigatedClassic = false;
  return new Promise(function (resolve, reject) {
    function poll() {
      tryCaptureFromTab(tabId, host, base, { skipNav: navigatedClassic }).then(function (result) {
        if (result && result.success) {
          resolve(result);
          return;
        }
        if (!navigatedClassic && Date.now() < deadline - 5000) {
          navigatedClassic = true;
          ensureClassicPage(tabId, base)
            .then(function () {
              setTimeout(poll, POLL_MS);
            })
            .catch(function () {
              setTimeout(poll, POLL_MS);
            });
          return;
        }
        if (Date.now() > deadline) {
          readSnowCookies(host).then(function (cookies) {
            if (hasSessionCookies(cookies)) {
              reject(
                new Error(
                  'Could not obtain g_ck after opening ServiceNow. Open arlo.service-now.com/navpage.do in a tab, wait for the home page, then click Connect again.'
                )
              );
              return;
            }
            reject(
              new Error(
                'Timed out waiting for ServiceNow login. Complete Okta sign-in in the ServiceNow tab, then click Connect again.'
              )
            );
          });
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
  let closeCreatedTab = false;

  return readSnowCookies(host)
    .then(function (cookies) {
      return findInstanceTabs(host).then(function (tabs) {
        var tabPromises = tabs.map(function (tab) {
          return tryCaptureFromTab(tab.id, host, base);
        });
        return Promise.all(tabPromises).then(function (results) {
          for (var i = 0; i < results.length; i++) {
            if (results[i] && results[i].success) {
              return results[i];
            }
          }
          if (hasSessionCookies(cookies) && tabs.length) {
            return waitForLoginAndCapture(host, tabs[0].id, base, 30000).catch(function () {
              return null;
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
        .create({ url: classicNavUrl(base) + '?gocview_connect=1', active: true })
        .then(function (tab) {
          createdTabId = tab.id;
          closeCreatedTab = false;
          return waitTabComplete(tab.id).then(function () {
            return waitForLoginAndCapture(host, tab.id, base, LOGIN_WAIT_MS);
          });
        });
    })
    .then(function (result) {
      if (result && result.success) {
        return result;
      }
      return {
        success: false,
        error:
          'Could not read ServiceNow session. Sign in at arlo.service-now.com, open /navpage.do, then click Connect again.',
      };
    })
    .finally(function () {
      if (createdTabId != null && closeCreatedTab) {
        chrome.tabs.remove(createdTabId).catch(function () {});
      }
    });
}

chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (msg && msg.type === 'GOCVIEW_CAPTURE_SNOW') {
    captureSnowSession(msg.instance)
      .then(function (result) {
        sendResponse(result || { success: false, error: 'No response from extension' });
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
