/**
 * OneView → Slack: send visible page text to the server;
 * AWS Bedrock summarizes it and posts via SLACK_WEBHOOK_URL.
 */
(function (global) {
    'use strict';

    var MAX_CLIENT_CHARS = 120000;

    function prepareRootClone(root) {
        const clone = root.cloneNode(true);
        clone.querySelectorAll('.slack-exclude, .result-actions').forEach(function (el) {
            el.remove();
        });
        clone.querySelectorAll('.sm-hover-tip').forEach(function (el) {
            el.remove();
        });
        clone.querySelectorAll('script, style, noscript, canvas').forEach(function (el) {
            el.remove();
        });
        return clone;
    }

    /** Plain text for Bedrock (no tooltips or action buttons). */
    function extractPagePlainText(root) {
        if (!root || !(root instanceof Element)) return '';
        const clone = prepareRootClone(root);
        let t = (clone.innerText || clone.textContent || '').replace(/\r\n/g, '\n');
        t = t.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
        if (t.length > MAX_CLIENT_CHARS) {
            t = t.slice(0, MAX_CLIENT_CHARS) + '\n\n[... truncated on client ...]';
        }
        return t;
    }

    function resolvePageTitle(options) {
        if (!options) return '';
        if (options.page_title && String(options.page_title).trim()) {
            return String(options.page_title).trim();
        }
        if (options.headerMrkdwn && String(options.headerMrkdwn).trim()) {
            return String(options.headerMrkdwn)
                .replace(/\*+/g, '')
                .replace(/<[^>]+>/g, '')
                .trim()
                .slice(0, 240);
        }
        if (options.fallbackPrefix && String(options.fallbackPrefix).trim()) {
            return String(options.fallbackPrefix).trim();
        }
        return '';
    }

    function parseJsonFromResponse(res) {
        return res.text().then(function (text) {
            let data = null;
            try {
                data = text ? JSON.parse(text) : {};
            } catch (_e) {
                data = {
                    success: false,
                    error: (text || '').slice(0, 280) || 'Respuesta no JSON del servidor',
                };
            }
            return { ok: res.ok, status: res.status, data: data };
        });
    }

    function toast(message, duration) {
        const ms = duration != null ? duration : 3000;
        if (typeof global.showNotification === 'function') {
            global.showNotification(message, ms);
            return;
        }
        const n = document.createElement('div');
        n.textContent = message;
        n.setAttribute('role', 'status');
        n.style.cssText =
            'position:fixed;bottom:24px;right:24px;max-width:min(380px,calc(100vw - 32px));z-index:99999;padding:14px 18px;background:#0f766e;color:#fff;font-weight:600;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,0.35);font-size:14px;font-family:system-ui,-apple-system,sans-serif;line-height:1.35;';
        document.body.appendChild(n);
        setTimeout(function () {
            n.remove();
        }, ms);
    }

    function postSummarizeFetch(page_text, options) {
        let text = (page_text || '').trim();
        if (text.length > MAX_CLIENT_CHARS) {
            text = text.slice(0, MAX_CLIENT_CHARS) + '\n\n[... truncated before send ...]';
        }
        if (!text) {
            toast('⚠️ No text to summarize', 5000);
            return;
        }
        const page_title = resolvePageTitle(options || {});
        let source_url = '';
        try {
            source_url = global.location.href || '';
        } catch (_e) {
            source_url = '';
        }
        toast('⏳ Bedrock: generating English summary (1–3 min)…', 15000);
        fetch('/api/slack/summarize-and-send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                page_text: text,
                page_title: page_title,
                source_url: source_url,
            }),
        })
            .then(parseJsonFromResponse)
            .then(function (ref) {
                const ok = ref.ok;
                const data = ref.data;
                if (ok && data.success) {
                    toast('✅ Summary sent to Slack', 6000);
                } else {
                    toast('❌ ' + (data && data.error ? data.error : 'Send failed'), 12000);
                }
            })
            .catch(function (err) {
                console.error(err);
                toast('❌ Error de red', 8000);
            });
    }

    /** Home only: sidebar + main column so the summary reflects the whole OneView UI. */
    function sendSummaryFromHomePage(options) {
        const aside = document.querySelector('aside.sidebar');
        const main = document.getElementById('slack-page-root');
        const half = Math.floor(MAX_CLIENT_CHARS / 2);
        const parts = [];
        if (aside) {
            let t = extractPagePlainText(aside);
            if (t.length > half) {
                t = t.slice(0, half) + '\n[... sidebar truncated ...]';
            }
            parts.push('=== SIDEBAR (history, deployments, Arlo status) ===\n' + t);
        }
        if (main) {
            let t = extractPagePlainText(main);
            if (t.length > half) {
                t = t.slice(0, half) + '\n[... main truncated ...]';
            }
            parts.push('=== MAIN (env hub, PagerDuty, Splunk, query, results) ===\n' + t);
        }
        const page_text = parts.join('\n\n').trim();
        if (page_text.length < 15) {
            toast('⚠️ Not enough content on the page yet', 5000);
            return;
        }
        const opts = Object.assign(
            { page_title: 'OneView — full page (sidebar + main workspace)' },
            options || {}
        );
        postSummarizeFetch(page_text, opts);
    }

    function sendSummaryFromElement(root, options) {
        if (!root) {
            toast('⚠️ No content to summarize', 5000);
            return;
        }
        const page_text = extractPagePlainText(root);
        if (!page_text.trim()) {
            toast('⚠️ No visible text to send', 5000);
            return;
        }
        postSummarizeFetch(page_text, options);
    }

    function sendSummaryFromSelector(selector, options) {
        const root = document.querySelector(selector);
        if (!root) {
            toast('⚠️ Content to summarize not found', 5000);
            return;
        }
        sendSummaryFromElement(root, options);
    }

    global.SlackShare = {
        parseJsonFromResponse: parseJsonFromResponse,
        sendFromSelector: sendSummaryFromSelector,
        sendSummaryFromSelector: sendSummaryFromSelector,
        sendSummaryFromElement: sendSummaryFromElement,
        sendSummaryFromHomePage: sendSummaryFromHomePage,
        extractPagePlainText: extractPagePlainText,
        toast: toast,
    };
})(window);
