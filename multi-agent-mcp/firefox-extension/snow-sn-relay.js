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

  chrome.runtime.sendMessage(
    { type: 'GOCVIEW_CAPTURE_SNOW', instance: location.origin },
    function (result) {
      var err = chrome.runtime.lastError;
      var payload = err
        ? { success: false, error: err.message }
        : result || { success: false, error: 'Sin respuesta' };
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
      window.close();
    }
  );
})();
