/**
 * Samsung external status board widget (PagerDuty status_dashboard_ids + public links).
 * Expects elements with ids: ss-board-links, ss-summary, ss-triggered-count, ss-ack-count-number,
 * ss-resolved-count-number, ss-active, ss-resolved, ss-time.
 * GET /api/pagerduty/samsung-monitor
 */
(function () {
    const API = '/api/pagerduty/samsung-monitor';
    const CACHE_KEY = 'samsung_status_monitor_v1';
    const TTL_MS = 170000;

    function esc(s) {
        return String(s == null ? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/"/g, '&quot;');
    }

    function applySamsungStatusMonitorPayload(data) {
        const timeElement = document.getElementById('ss-time');
        if (timeElement) {
            const now = new Date();
            timeElement.textContent =
                'Last updated: ' +
                now.toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                });
        }

        const boardLinks = document.getElementById('ss-board-links');
        if (boardLinks) {
            const aOpen = function (href, label) {
                return (
                    '<a href="' +
                    esc(href) +
                    '" target="_blank" rel="noopener noreferrer" style="color: var(--link-color, #0284c7); font-weight: 600;">' +
                    esc(label) +
                    '</a>'
                );
            };
            if (data.disabled) {
                boardLinks.innerHTML =
                    '<span style="opacity:0.9;">Samsung status board disabled (set <code>SAMSUNG_STATUS_DASHBOARD_ID</code>).</span>';
            } else if (data.error && !data.triggered && data.triggered !== 0) {
                boardLinks.innerHTML = '<span style="color:#dc2626;">⚠️ ' + esc(data.error) + '</span>';
            } else {
                const id = data.status_dashboard_id || 'PRBJIO4';
                const sub = 'arlo';
                const base =
                    data.status_dashboard_url ||
                    'https://' + sub + '.pagerduty.com/external-status-dashboard/' + id + '/incidents';
                // Samsung status page: Resolved → Ongoing → Pending (misma convención que la UI pública de PD).
                // Ongoing en el navegador usa ?tab=active; Resolved ?tab=resolved; Pending ?tab=pending.
                const basePath = base.split('?')[0];
                const uResolved =
                    data.status_dashboard_url_resolved || basePath + '?tab=resolved';
                const uOngoing =
                    data.status_dashboard_url_active || basePath + '?tab=active';
                const uPending =
                    data.status_dashboard_url_pending || basePath + '?tab=pending';
                boardLinks.innerHTML =
                    '<span style="opacity:0.85;font-weight:700;">External status</span> · board <code style="font-size:10px;">' +
                    esc(id) +
                    '</code><br>' +
                    aOpen(uResolved, 'Resolved') +
                    ' · ' +
                    aOpen(uOngoing, 'Ongoing') +
                    ' · ' +
                    aOpen(uPending, 'Pending') +
                    ' · ' +
                    aOpen(base, 'All incidents');
            }
        }

        const triggered = data.triggered || 0;
        const acknowledged = data.acknowledged || 0;
        const resolved = data.resolved || 0;

        const triggeredCountEl = document.getElementById('ss-triggered-count');
        const ackCountEl = document.getElementById('ss-ack-count-number');
        const resolvedCountEl = document.getElementById('ss-resolved-count-number');
        if (triggeredCountEl) triggeredCountEl.textContent = triggered;
        if (ackCountEl) ackCountEl.textContent = acknowledged;
        if (resolvedCountEl) resolvedCountEl.textContent = resolved;

        const summaryElement = document.getElementById('ss-summary');
        if (summaryElement) {
            if (data.error && !data.disabled) {
                summaryElement.style.background = '#dc2626';
                summaryElement.classList.add('pd-status-blink');
            } else if (data.disabled) {
                summaryElement.style.background = '#64748b';
                summaryElement.classList.remove('pd-status-blink');
            } else if (triggered > 0) {
                summaryElement.style.background = '#dc2626';
                summaryElement.classList.add('pd-status-blink');
            } else if (acknowledged > 0) {
                summaryElement.style.background = '#f59e0b';
                summaryElement.classList.add('pd-status-blink');
            } else {
                /* Match main PagerDuty Status card (healthy = green) */
                summaryElement.style.background = '#10b981';
                summaryElement.classList.remove('pd-status-blink');
            }
        }

        const activeElement = document.getElementById('ss-active');
        if (activeElement) {
            if (data.error && !data.disabled) {
                activeElement.innerHTML =
                    '<li style="color: #f56565; border-left-color: #f56565;">⚠️ Unable to load</li>';
            } else if (data.disabled) {
                activeElement.innerHTML = '<li style="color:#94a3b8;">—</li>';
            } else if (!data.active || data.active.length === 0) {
                activeElement.innerHTML =
                    '<li style="color: #48bb78; border-left-color: #48bb78;">✅ No active incidents</li>';
            } else {
                activeElement.innerHTML = data.active
                    .map(function (inc) {
                        const st = inc && inc.status ? String(inc.status).toLowerCase() : 'unknown';
                        const statusClass = st;
                        const icon = st === 'triggered' ? '🔴' : '🟡';
                        const url = (inc && inc.url) ? inc.url : '#';
                        return (
                            '<li class="' +
                            statusClass +
                            '" title="' +
                            esc(inc.title) +
                            "\" onclick=\"window.open('" +
                            esc(url).replace(/'/g, '%27') +
                            '\', \'_blank\')" style="cursor: pointer;">' +
                            '<strong>' +
                            icon +
                            ' #' +
                            esc(inc.number) +
                            '</strong>' +
                            '<div style="color: var(--text-secondary); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">' +
                            esc(inc.service) +
                            '</div></li>'
                        );
                    })
                    .join('');
            }
        }

        const resolvedElement = document.getElementById('ss-resolved');
        if (resolvedElement) {
            if (data.error && !data.disabled) {
                resolvedElement.innerHTML =
                    '<li style="color: #f56565; border-left-color: #f56565;">⚠️ Unable to load</li>';
            } else if (data.disabled) {
                resolvedElement.innerHTML = '<li style="color:#94a3b8;">—</li>';
            } else if (!data.recently_resolved || data.recently_resolved.length === 0) {
                resolvedElement.innerHTML = '<li style="color: #999;">No resolved incidents</li>';
            } else {
                resolvedElement.innerHTML = data.recently_resolved
                    .map(function (inc) {
                        const url = inc.url || '#';
                        return (
                            '<li class="resolved" title="' +
                            esc(inc.title) +
                            "\" onclick=\"window.open('" +
                            esc(url).replace(/'/g, '%27') +
                            '\', \'_blank\')" style="cursor: pointer;">' +
                            '<strong>🟢 #' +
                            esc(inc.number) +
                            '</strong>' +
                            '<div style="color: var(--text-secondary); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">' +
                            esc(inc.service) +
                            '</div></li>'
                        );
                    })
                    .join('');
            }
        }
    }

    function loadSamsungStatusWidget(forceRefresh) {
        if (!document.getElementById('ss-summary')) return;
        const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
        if (!forceRefresh && C) {
            const hit = C.get(CACHE_KEY);
            if (hit) {
                applySamsungStatusMonitorPayload(hit);
                return;
            }
        }
        var ac = new AbortController();
        var abortTimer = setTimeout(function () {
            ac.abort();
        }, 90000);
        fetch(API, { signal: ac.signal })
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (data) {
                if (C) {
                    C.set(CACHE_KEY, data, TTL_MS);
                }
                applySamsungStatusMonitorPayload(data);
            })
            .catch(function (err) {
                console.error('Samsung status widget:', err);
                var msg = err && err.name === 'AbortError' ? 'Request timed out (90s)' : err.message || String(err);
                applySamsungStatusMonitorPayload({
                    error: msg,
                });
            })
            .finally(function () {
                clearTimeout(abortTimer);
            });
    }

    window.loadSamsungStatusWidget = loadSamsungStatusWidget;
    window.applySamsungStatusMonitorPayload = applySamsungStatusMonitorPayload;

    function samsungIntervalTick() {
        if (document.getElementById('ss-summary')) {
            loadSamsungStatusWidget(false);
        }
    }

    function boot() {
        samsungIntervalTick();
        setInterval(samsungIntervalTick, 180000);
    }
    document.addEventListener('DOMContentLoaded', function () {
        if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(boot, { timeout: 2500 });
        } else {
            setTimeout(boot, 400);
        }
    });
})();
