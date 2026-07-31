'use strict';

(function () {
  if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.sendMessage) {
    return;
  }
  if (!window.opener) {
    return;
  }

  var params = new URLSearchParams(location.search);
  if (params.get('gocview_connect') !== '1') {
    return;
  }

  var deadline = Date.now() + 180000;
  var done = false;

  function finish(payload) {
    if (done) return;
    done = true;
    window.opener.postMessage(
      {
        type: 'GOCVIEW_SNOW_POPUP_RESULT',
        success: payload.success,
        cookies: payload.cookies,
        user_token: payload.user_token,
        error: payload.error,
      },
      '*'
    );
    setTimeout(function () {
      window.close();
    }, 300);
  }

  function attempt() {
    chrome.runtime.sendMessage(
      { type: 'GOCVIEW_CAPTURE_SNOW', instance: location.origin },
      function (result) {
        var err = chrome.runtime.lastError;
        var payload = err
          ? { success: false, error: err.message }
          : result || { success: false, error: 'No response' };
        if (payload.success) {
          finish(payload);
          return;
        }
        if (Date.now() > deadline) {
          finish({
            success: false,
            error: payload.error || 'Timed out waiting for ServiceNow login',
          });
          return;
        }
        setTimeout(attempt, 1500);
      }
    );
  }

  attempt();
})();
