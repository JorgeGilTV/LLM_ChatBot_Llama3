'use strict';

(function () {
  if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.sendMessage) {
    return;
  }

  window.postMessage({ type: 'GOCVIEW_EXTENSION_READY', feature: 'snow_connect' }, '*');

  window.addEventListener('message', function (event) {
    if (event.source !== window) {
      return;
    }
    var data = event.data;
    if (!data || data.type !== 'GOCVIEW_SNOW_CONNECT_REQUEST') {
      return;
    }

    chrome.runtime.sendMessage(
      { type: 'GOCVIEW_CAPTURE_SNOW', instance: data.instance },
      function (result) {
        var err = chrome.runtime.lastError;
        var payload = err
          ? { success: false, error: err.message }
          : result || { success: false, error: 'Sin respuesta de la extensión' };
        window.postMessage(
          {
            type: 'GOCVIEW_SNOW_CONNECT_RESPONSE',
            requestId: data.requestId,
            success: payload.success,
            cookies: payload.cookies,
            user_token: payload.user_token,
            error: payload.error,
          },
          '*'
        );
      }
    );
  });
})();
