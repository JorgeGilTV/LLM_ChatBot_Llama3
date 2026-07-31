"""ServiceNow session-cookie auth (manual Okta login in browser, no OAuth app required)."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_log = logging.getLogger(__name__)

DEFAULT_SNOW_INSTANCE = "https://arlo.service-now.com"
_HTTP_TIMEOUT = (10, 45)
SESSION_COOKIES_KEY = "snow_cookies"
SESSION_RAW_KEY = "snow_cookie_raw"
SESSION_USER_TOKEN_KEY = "snow_user_token"

# Cookies that commonly appear on Okta/SSO ServiceNow sessions
_IMPORTANT_COOKIES = frozenset(
    {
        "glide_session_store",
        "jsessionid",
        "glide_user_route",
        "glide_node_id_for_js",
        "ux-token",
    }
)


def _snow_instance() -> str:
    return (os.getenv("SNOW_INSTANCE") or DEFAULT_SNOW_INSTANCE).rstrip("/")


def _touch_session(flask_session: dict[str, Any]) -> None:
    if hasattr(flask_session, "modified"):
        flask_session.modified = True


def _strip_cookie_prefix(raw: str) -> str:
    text = (raw or "").strip()
    if text.lower().startswith("cookie:"):
        return text.split(":", 1)[1].strip()
    return text


def parse_cookie_blob(raw: str) -> dict[str, str]:
    """Parse `name=value; name2=value2`, bare glide value, or full Cookie header."""
    text = _strip_cookie_prefix(raw)
    if not text:
        return {}

    out: dict[str, str] = {}
    for part in re.split(r"[;\n]+", text):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            if "glide_session_store" not in out:
                out["glide_session_store"] = piece
            continue
        name, _, value = piece.partition("=")
        name = name.strip()
        value = value.strip().strip('"')
        if name:
            out[name] = value
    return out


def cookie_header_from_dict(cookies: dict[str, str]) -> str:
    if not cookies:
        return ""
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    lower_map = {k.lower(): k for k in cookies}
    for preferred in ("JSESSIONID", "glide_session_store", "glide_user_route"):
        key = lower_map.get(preferred.lower())
        if key and key not in seen:
            ordered.append((key, cookies[key]))
            seen.add(key)
    for name, value in cookies.items():
        if name not in seen:
            ordered.append((name, value))
    return "; ".join(f"{name}={value}" for name, value in ordered)


def _server_session_path() -> Path:
    root = Path(os.getenv("SNOW_SESSION_FILE") or os.path.join(os.getcwd(), "data", "snow_server_session.json"))
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def load_persisted_server_session() -> tuple[dict[str, str], str]:
    path = _server_session_path()
    if not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, ""
        cookies = data.get("cookies") if isinstance(data.get("cookies"), dict) else {}
        token = str(data.get("user_token") or "").strip()
        return {str(k): str(v) for k, v in cookies.items() if v}, token
    except Exception as e:
        _log.warning("Could not read persisted ServiceNow session: %s", e)
        return {}, ""


def persist_server_session(cookies: dict[str, str], user_token: str | None = None) -> None:
    flag = (os.getenv("SNOW_PERSIST_SESSION") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    token = (user_token or "").strip()
    if not cookies or not token:
        return
    try:
        _server_session_path().write_text(
            json.dumps({"cookies": cookies, "user_token": token}, indent=0),
            encoding="utf-8",
        )
        _log.info("Persisted ServiceNow session to %s", _server_session_path())
    except Exception as e:
        _log.warning("Could not persist ServiceNow session: %s", e)


def get_raw_cookie_header(flask_session: dict[str, Any] | None) -> str:
    if flask_session is not None:
        raw = (flask_session.get(SESSION_RAW_KEY) or "").strip()
        if raw:
            return _strip_cookie_prefix(raw)

    env_raw = (os.getenv("SNOW_SESSION_COOKIE") or "").strip()
    if env_raw:
        return _strip_cookie_prefix(env_raw)

    file_cookies, _ = load_persisted_server_session()
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


def get_user_token(flask_session: dict[str, Any] | None) -> str:
    if flask_session is not None:
        stored = (flask_session.get(SESSION_USER_TOKEN_KEY) or "").strip()
        if stored:
            return stored
    env_token = (os.getenv("SNOW_USER_TOKEN") or os.getenv("SNOW_G_CK") or "").strip()
    if env_token:
        return env_token
    _, file_token = load_persisted_server_session()
    return file_token


def save_session_auth(
    flask_session: dict[str, Any],
    cookies: dict[str, str],
    *,
    raw_header: str | None = None,
    user_token: str | None = None,
) -> None:
    save_cookies(flask_session, cookies, raw_header=raw_header)
    token = (user_token or "").strip()
    if token:
        flask_session[SESSION_USER_TOKEN_KEY] = token
    _touch_session(flask_session)
    persist_server_session(get_stored_cookies(flask_session), token)


def clear_cookies(flask_session: dict[str, Any]) -> None:
    flask_session.pop(SESSION_COOKIES_KEY, None)
    flask_session.pop(SESSION_RAW_KEY, None)
    flask_session.pop(SESSION_USER_TOKEN_KEY, None)
    _touch_session(flask_session)


def cookie_session_connected(flask_session: dict[str, Any] | None) -> bool:
    header = get_raw_cookie_header(flask_session)
    if header and len(header) > 20:
        return True
    cookies = get_stored_cookies(flask_session)
    if not cookies:
        return False
    keys = {k.lower() for k in cookies}
    return bool(keys & _IMPORTANT_COOKIES)


def server_env_auth_available() -> bool:
    """Server .env has a ServiceNow browser session (works for all users / MCP without OAuth)."""
    return cookie_session_connected(None)


def cookie_names_hint(flask_session: dict[str, Any] | None) -> list[str]:
    cookies = get_stored_cookies(flask_session)
    return sorted(cookies.keys(), key=str.lower)


def api_requests_session_from_cookies(flask_session: dict[str, Any] | None) -> requests.Session | None:
    if not cookie_session_connected(flask_session):
        return None

    header = get_raw_cookie_header(flask_session)
    if not header:
        header = cookie_header_from_dict(get_stored_cookies(flask_session))
    if not header:
        return None

    host = urlparse(_snow_instance()).hostname or "arlo.service-now.com"
    sess = requests.Session()
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": header,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": f"{_snow_instance()}/navpage.do",
        "Origin": _snow_instance(),
    }
    user_token = get_user_token(flask_session)
    if user_token:
        headers["X-UserToken"] = user_token
    sess.headers.update(headers)
    # Also attach to jar for redirects
    for name, value in parse_cookie_blob(header).items():
        sess.cookies.set(name, value, domain=host, path="/")
    return sess


def _auth_error_hint(flask_session: dict[str, Any] | None) -> str:
    names = {n.lower() for n in cookie_names_hint(flask_session)}
    parts: list[str] = []
    if names:
        parts.append("Detected: " + ", ".join(sorted(names)))
    if not get_user_token(flask_session):
        parts.append(
            "Missing g_ck token: in ServiceNow open Develop → Web Inspector → Console, run window.g_ck and copy the value."
        )
    if "glide_user_route" not in names:
        parts.append("Optional: add glide_user_route (Storage → Cookies).")
    return " ".join(parts)


def validate_session(flask_session: dict[str, Any] | None) -> tuple[bool, str]:
    """Quick REST probe to verify cookies still work."""
    sess = api_requests_session_from_cookies(flask_session)
    if sess is None:
        return False, "Session cookie is invalid or empty."

    inst = _snow_instance()
    probes = (
        f"{inst}/api/now/stats/incident",
        f"{inst}/api/now/table/incident",
    )
    last_body = ""
    last_code = 0
    try:
        for url in probes:
            params = (
                {"sysparm_count": "true", "sysparm_query": "active=true"}
                if "stats" in url
                else {"sysparm_limit": "1", "sysparm_fields": "number"}
            )
            r = sess.get(url, params=params, timeout=_HTTP_TIMEOUT)
            last_code = r.status_code
            last_body = (r.text or "")[:200]
            if r.status_code == 200:
                return True, ""
        if last_code == 401:
            return False, (
                "Session expired or incomplete cookie (401). "
                + _auth_error_hint(flask_session)
            )
        if last_code == 403:
            return False, (
                "ServiceNow denied access (403). Your user may not have REST permission. "
                + _auth_error_hint(flask_session)
            )
        return False, f"ServiceNow HTTP {last_code}: {last_body}"
    except Exception as e:
        return False, str(e)
