/**
 * ADT external status board (PagerDuty status_dashboard_ids + public links).
 * Elements: adt-board-links, adt-summary, adt-triggered-count, adt-ack-count-number,
 * adt-resolved-count-number, adt-active, adt-resolved, adt-time.
 * GET /api/pagerduty/adt-monitor
 */
(function () {
    const API = '/api/pagerduty/adt-monitor';
    const CACHE_KEY = 'adt_status_monitor_v1';
    const TTL_MS = 350000;

    function esc(s) {
        return String(s == null ? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/"/g, '&quot;');
    }

    function applyAdtStatusMonitorPayload(data) {
        const timeElement = document.getElementById('adt-time');
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

        const boardLinks = document.getElementById('adt-board-links');
        if (boardLinks) {
            const aOpen = function (href, label) {
                return (
                    '<a href="' +
                    esc(href) +
                    '" target="_blank" rel="noopener noreferrer" style="color: var(--link-color, #4f46e5); font-weight: 600;">' +
                    esc(label) +
                    '</a>'
                );
            };
            if (data.disabled) {
                boardLinks.innerHTML =
                    '<span style="opacity:0.9;">ADT status board disabled (set <code>ADT_STATUS_DASHBOARD_ID</code>).</span>';
            } else if (data.error && !data.triggered && data.triggered !== 0) {
                boardLinks.innerHTML = '<span style="color:#dc2626;">⚠️ ' + esc(data.error) + '</span>';
            } else {
                const id = data.status_dashboard_id || 'PK1QF1G';
                const sub = 'arlo';
                const base =
                    data.status_dashboard_url ||
                    'https://' + sub + '.pagerduty.com/external-status-dashboard/' + id + '/incidents';
                // ADT status page: Resolved → Ongoing → Pending (misma convención que Samsung / UI pública PD).
                // Ongoing ?tab=active; Resolved ?tab=resolved; Pending ?tab=pending.
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

        const triggeredCountEl = document.getElementById('adt-triggered-count');
        const ackCountEl = document.getElementById('adt-ack-count-number');
        const resolvedCountEl = document.getElementById('adt-resolved-count-number');
        if (triggeredCountEl) triggeredCountEl.textContent = triggered;
        if (ackCountEl) ackCountEl.textContent = acknowledged;
        if (resolvedCountEl) resolvedCountEl.textContent = resolved;

        const summaryElement = document.getElementById('adt-summary');
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

        const activeElement = document.getElementById('adt-active');
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

        const resolvedElement = document.getElementById('adt-resolved');
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

    function loadAdtStatusWidget(forceRefresh) {
        if (!document.getElementById('adt-summary')) return;
        const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
        if (!forceRefresh && C) {
            const hit = C.get(CACHE_KEY);
            if (hit) {
                applyAdtStatusMonitorPayload(hit);
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
                applyAdtStatusMonitorPayload(data);
            })
            .catch(function (err) {
                console.error('ADT status widget:', err);
                var msg = err && err.name === 'AbortError' ? 'Request timed out (90s)' : err.message || String(err);
                applyAdtStatusMonitorPayload({
                    error: msg,
                });
            })
            .finally(function () {
                clearTimeout(abortTimer);
            });
    }

    window.loadAdtStatusWidget = loadAdtStatusWidget;
    window.applyAdtStatusMonitorPayload = applyAdtStatusMonitorPayload;

    function adtIntervalTick() {
        if (document.getElementById('adt-summary')) {
            loadAdtStatusWidget(false);
        }
    }

    function boot() {
        adtIntervalTick();
        setInterval(adtIntervalTick, 360000);
    }
    document.addEventListener('DOMContentLoaded', function () {
        if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(boot, { timeout: 2500 });
        } else {
            setTimeout(boot, 400);
        }
    });
})();
