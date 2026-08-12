"""Piranha (EngiHub) session — Okta SSO via AWS ALB auth cookies."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from tools.servicenow_session import cookie_header_from_dict, parse_cookie_blob

_log = logging.getLogger(__name__)

DEFAULT_PIRANHA_BASE = "https://piranha.arlo.com"
_HTTP_TIMEOUT = (10, 45)
SESSION_COOKIES_KEY = "piranha_cookies"
SESSION_RAW_KEY = "piranha_cookie_raw"


def _piranha_base() -> str:
    return (os.getenv("PIRANHA_BASE_URL") or DEFAULT_PIRANHA_BASE).rstrip("/")


def _piranha_host() -> str:
    return urlparse(_piranha_base()).hostname or "piranha.arlo.com"


def _touch_session(flask_session: dict[str, Any]) -> None:
    if hasattr(flask_session, "modified"):
        flask_session.modified = True


def _server_session_path() -> Path:
    root = Path(
        os.getenv("PIRANHA_SESSION_FILE")
        or os.path.join(os.getcwd(), "data", "piranha_server_session.json")
    )
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def load_persisted_server_session() -> dict[str, str]:
    path = _server_session_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies") if isinstance(data, dict) else {}
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if v}
    except Exception as e:
        _log.warning("Could not read persisted Piranha session: %s", e)
    return {}


def persist_server_session(cookies: dict[str, str]) -> None:
    flag = (os.getenv("PIRANHA_PERSIST_SESSION") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    if not cookies:
        return
    try:
        _server_session_path().write_text(
            json.dumps({"cookies": cookies}, indent=0),
            encoding="utf-8",
        )
        _log.info("Persisted Piranha session to %s", _server_session_path())
    except Exception as e:
        _log.warning("Could not persist Piranha session: %s", e)


def _strip_cookie_prefix(raw: str) -> str:
    text = (raw or "").strip()
    if text.lower().startswith("cookie:"):
        return text.split(":", 1)[1].strip()
    return text


def get_raw_cookie_header(flask_session: dict[str, Any] | None) -> str:
    if flask_session is not None:
        raw = (flask_session.get(SESSION_RAW_KEY) or "").strip()
        if raw:
            return _strip_cookie_prefix(raw)

    env_raw = (os.getenv("PIRANHA_SESSION_COOKIE") or "").strip()
    if env_raw:
        return _strip_cookie_prefix(env_raw)

    file_cookies = load_persisted_server_session()
    if file_cookies:
        return cookie_header_from_dict(file_cookies)
    return ""


def get_stored_cookies(flask_session: dict[str, Any] | None) -> dict[str, str]:
    if flask_session is not None:
        stored = flask_session.get(SESSION_COOKIES_KEY)
        if isinstance(stored, dict) and stored:
            return {str(k): str(v) for k, v in stored.items() if v}

    raw = get_raw_cookie_header(flask_session)
    if raw:
        return parse_cookie_blob(raw)
    return {}


def save_cookies(
    flask_session: dict[str, Any],
    cookies: dict[str, str],
    *,
    raw_header: str | None = None,
) -> None:
    header = _strip_cookie_prefix(raw_header or "")
    if not header and cookies:
        header = cookie_header_from_dict(cookies)
    if header:
        flask_session[SESSION_RAW_KEY] = header
    if cookies:
        flask_session[SESSION_COOKIES_KEY] = {str(k): str(v) for k, v in cookies.items() if k and v}
    elif header:
        flask_session[SESSION_COOKIES_KEY] = parse_cookie_blob(header)
    _touch_session(flask_session)
    persist_server_session(get_stored_cookies(flask_session))


def clear_cookies(flask_session: dict[str, Any]) -> None:
    flask_session.pop(SESSION_COOKIES_KEY, None)
    flask_session.pop(SESSION_RAW_KEY, None)
    _touch_session(flask_session)


def _has_alb_session(cookies: dict[str, str]) -> bool:
    for name in cookies:
        lower = name.lower()
        if lower.startswith("awselbauthsessioncookie"):
            return True
    return False


def cookie_session_connected(flask_session: dict[str, Any] | None) -> bool:
    header = get_raw_cookie_header(flask_session)
    if header and len(header) > 30:
        cookies = parse_cookie_blob(header)
        return _has_alb_session(cookies)
    cookies = get_stored_cookies(flask_session)
    return _has_alb_session(cookies)


def server_env_auth_available() -> bool:
    return cookie_session_connected(None)


def cookie_names_hint(flask_session: dict[str, Any] | None) -> list[str]:
    return sorted(get_stored_cookies(flask_session).keys(), key=str.lower)


def api_requests_session_from_cookies(flask_session: dict[str, Any] | None) -> requests.Session | None:
    if not cookie_session_connected(flask_session):
        return None

    header = get_raw_cookie_header(flask_session)
    if not header:
        header = cookie_header_from_dict(get_stored_cookies(flask_session))
    if not header:
        return None

    host = _piranha_host()
    base = _piranha_base()
    sess = requests.Session()
    sess.headers.update(
        {
            "Accept": "application/json",
            "Cookie": header,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": f"{base}/#employees",
            "Origin": base,
        }
    )
    for name, value in parse_cookie_blob(header).items():
        sess.cookies.set(name, value, domain=host, path="/")
    return sess


def validate_session(flask_session: dict[str, Any] | None) -> tuple[bool, str]:
    sess = api_requests_session_from_cookies(flask_session)
    if sess is None:
        return False, "Piranha session cookie is invalid or empty."

    api_path = (os.getenv("PIRANHA_EMPLOYEES_API") or "/api/2/employees").strip()
    if not api_path.startswith("/"):
        api_path = "/" + api_path
    url = f"{_piranha_base()}{api_path}"

    try:
        r = sess.get(url, timeout=_HTTP_TIMEOUT, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location") or ""
            if "okta.com" in loc.lower():
                return False, "Session expired — Okta login required. Reconnect Piranha."
            return False, f"Piranha redirected (HTTP {r.status_code})."
        if r.status_code == 401:
            return False, "Piranha rejected session (401). Paste fresh ALB cookies after Okta login."
        if r.status_code == 403:
            return False, "Piranha denied access (403)."
        if r.status_code != 200:
            return False, f"Piranha HTTP {r.status_code}: {(r.text or '')[:180]}"
        try:
            payload = r.json()
        except ValueError:
            return False, "Piranha returned non-JSON (session may be invalid)."
        if isinstance(payload, list) or isinstance(payload, dict):
            return True, ""
        return False, "Unexpected Piranha API response shape."
    except Exception as e:
        return False, str(e)


def connect_instructions_html() -> str:
    base = _piranha_base()
    return (
        f"<div style='background:#eff6ff;padding:12px;border-left:4px solid #2563eb;"
        f"border-radius:6px;margin:10px 0;font-size:13px;color:#1e3a8a;'>"
        f"<strong>Connect Piranha (Okta)</strong><br>"
        f"1. Open <a href='{base}/#employees' target='_blank' rel='noopener'>Piranha Employees</a> "
        f"and sign in with Okta.<br>"
        f"2. DevTools → Application → Cookies → <code>{_piranha_host()}</code><br>"
        f"3. Copy <code>AWSELBAuthSessionCookie-0</code> and "
        f"<code>AWSELBAuthSessionCookie-1</code> (if present) as "
        f"<code>name=value; name2=value2</code>.<br>"
        f"4. POST to <code>/api/piranha/session</code> or use "
        f"<strong>Connect Piranha (Okta)</strong> in GocView (local Playwright).<br>"
        f"Or set <code>PIRANHA_SESSION_COOKIE</code> in server <code>.env</code>.</div>"
    )
