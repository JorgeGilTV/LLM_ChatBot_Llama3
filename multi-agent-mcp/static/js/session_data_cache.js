/**
 * sessionStorage-backed cache for API JSON/HTML payloads (same browser tab session).
 * Reduces repeat calls when navigating between home, /statusmonitor hub, and env drill-downs.
 */
(function (global) {
    var PREFIX = 'mcp_sess_v1_';
    var DEFAULT_TTL_MS = 150000;

    function wrap(k) {
        return PREFIX + k;
    }

    function get(key) {
        try {
            var raw = sessionStorage.getItem(wrap(key));
            if (!raw) return null;
            var o = JSON.parse(raw);
            if (!o || typeof o.t !== 'number') return null;
            var ttl = typeof o.ttl === 'number' ? o.ttl : DEFAULT_TTL_MS;
            if (Date.now() - o.t > ttl) return null;
            return o.v;
        } catch (e) {
            return null;
        }
    }

    function set(key, value, ttlMs) {
        try {
            var payload = JSON.stringify({
                t: Date.now(),
                ttl: ttlMs != null ? ttlMs : DEFAULT_TTL_MS,
                v: value
            });
            if (payload.length > 4500000) {
                console.warn('SessionDataCache: payload too large, skip', key);
                return false;
            }
            sessionStorage.setItem(wrap(key), payload);
            return true;
        } catch (e) {
            console.warn('SessionDataCache set failed', key, e);
            return false;
        }
    }

    function remove(key) {
        try {
            sessionStorage.removeItem(wrap(key));
        } catch (e) {}
    }

    function clearAll() {
        try {
            for (var i = sessionStorage.length - 1; i >= 0; i--) {
                var k = sessionStorage.key(i);
                if (k && k.indexOf(PREFIX) === 0) sessionStorage.removeItem(k);
            }
        } catch (e) {}
    }

    global.SessionDataCache = {
        get: get,
        set: set,
        remove: remove,
        clearAll: clearAll,
        DEFAULT_TTL_MS: DEFAULT_TTL_MS
    };
})(typeof window !== 'undefined' ? window : global);
