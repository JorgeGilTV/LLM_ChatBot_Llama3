"""
Outbound HTTPS to Slack (Incoming Webhooks and api.slack.com).

Handles transient TLS/network errors (common behind corporate proxies or unstable links)
with retries. Optional SSL verification disable for broken environments (last resort).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Mapping

import requests

_LOG = logging.getLogger(__name__)


def slack_ssl_verify_enabled() -> bool:
    """If false, TLS certificate verification is disabled (insecure; debugging only)."""
    v = (os.getenv("SLACK_SSL_VERIFY") or os.getenv("SLACK_WEBHOOK_SSL_VERIFY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def slack_webhook_attempts() -> int:
    try:
        n = int((os.getenv("SLACK_WEBHOOK_REQUEST_ATTEMPTS") or "3").strip())
    except (TypeError, ValueError):
        n = 3
    return max(1, min(n, 8))


def format_slack_connection_error(exc: BaseException) -> str:
    """User-visible hint for SSL/proxy/VPN failures."""
    msg = str(exc)
    if "SSL" in msg or "ssl" in msg.lower() or "EOF" in msg or "UNEXPECTED_EOF" in msg:
        return (
            msg
            + "\n\n"
            "This often indicates a corporate proxy/VPN/firewall interfering with HTTPS, "
            "or an outdated Python/OpenSSL. Try: export REQUESTS_CA_BUNDLE=/path/to/corp-ca-bundle.pem "
            "(or install your org root CA). If you must (insecure), set SLACK_SSL_VERIFY=0 — "
            "only on trusted networks."
        )
    return msg


def post_incoming_webhook(
    webhook_url: str,
    payload: Mapping[str, Any],
    *,
    timeout: tuple[float, float] | float = (15, 60),
) -> requests.Response:
    """
    POST JSON to Slack Incoming Webhook (hooks.slack.com) with retries on TLS/connection errors.
    """
    verify = slack_ssl_verify_enabled()
    if not verify:
        _LOG.warning(
            "SLACK_SSL_VERIFY disabled: TLS verification is off for Slack webhooks (insecure)."
        )
    headers = {"Content-Type": "application/json"}
    attempts = slack_webhook_attempts()
    for attempt in range(attempts):
        try:
            return requests.post(
                webhook_url,
                json=dict(payload),
                timeout=timeout,
                headers=headers,
                verify=verify,
            )
        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
        ) as e:
            if attempt < attempts - 1:
                delay = 0.35 * (2**attempt)
                _LOG.warning(
                    "Slack Incoming Webhook failed (%s/%s): %s; retry in %.1fs",
                    attempt + 1,
                    attempts,
                    e,
                    delay,
                )
                time.sleep(delay)
            else:
                raise


def post_slack_api(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, Any] | None = None,
    files: Any = None,
    timeout: tuple[float, float] | float = (15, 90),
) -> requests.Response:
    """POST to api.slack.com (e.g. files.upload) with same SSL policy and retries."""
    verify = slack_ssl_verify_enabled()
    if not verify:
        _LOG.warning("SLACK_SSL_VERIFY disabled: TLS verification is off for Slack API (insecure).")
    attempts = slack_webhook_attempts()
    hdr = dict(headers) if headers else {}
    for attempt in range(attempts):
        try:
            return requests.post(
                url,
                headers=hdr,
                data=data,
                files=files,
                timeout=timeout,
                verify=verify,
            )
        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
        ) as e:
            if attempt < attempts - 1:
                delay = 0.35 * (2**attempt)
                _LOG.warning(
                    "Slack API POST failed (%s/%s): %s; retry in %.1fs",
                    attempt + 1,
                    attempts,
                    e,
                    delay,
                )
                time.sleep(delay)
            else:
                raise
