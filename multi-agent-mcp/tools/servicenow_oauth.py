"""ServiceNow OAuth 2.0 (Authorization Code + PKCE) for Okta/SSO login."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import requests

DEFAULT_SNOW_INSTANCE = "https://arlo.service-now.com"
_HTTP_TIMEOUT = (10, 45)

SESSION_KEY = "snow_oauth"
PKCE_KEY = "snow_oauth_pkce"
STATE_KEY = "snow_oauth_state"
RETURN_KEY = "snow_oauth_return"


def _snow_instance() -> str:
    return (os.getenv("SNOW_INSTANCE") or DEFAULT_SNOW_INSTANCE).rstrip("/")


def oauth_client_id() -> str:
    return (os.getenv("SNOW_OAUTH_CLIENT_ID") or "").strip()


def oauth_client_secret() -> str:
    return (os.getenv("SNOW_OAUTH_CLIENT_SECRET") or "").strip()


def oauth_redirect_uri(request_root: str | None = None) -> str:
    explicit = (os.getenv("SNOW_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    root = (request_root or os.getenv("GOCVIEW_PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")
    return f"{root}/oauth/snow/callback"


def oauth_configured() -> bool:
    return bool(oauth_client_id())


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def get_token_bundle(flask_session: dict[str, Any]) -> dict[str, Any] | None:
    bundle = flask_session.get(SESSION_KEY)
    if not isinstance(bundle, dict):
        return None
    token = (bundle.get("access_token") or "").strip()
    if not token:
        return None
    expires_at = float(bundle.get("expires_at") or 0)
    if expires_at and time.time() >= expires_at - 60:
        refreshed = refresh_token_bundle(flask_session)
        if refreshed:
            return flask_session.get(SESSION_KEY)
        return None
    return bundle


def save_token_bundle(flask_session: dict[str, Any], token_response: dict[str, Any]) -> None:
    expires_in = int(token_response.get("expires_in") or 3600)
    flask_session[SESSION_KEY] = {
        "access_token": token_response.get("access_token"),
        "refresh_token": token_response.get("refresh_token"),
        "token_type": token_response.get("token_type") or "Bearer",
        "scope": token_response.get("scope"),
        "expires_at": time.time() + max(60, expires_in),
    }
    _touch_session(flask_session)


def _touch_session(flask_session: dict[str, Any]) -> None:
    if hasattr(flask_session, "modified"):
        flask_session.modified = True


def clear_token_bundle(flask_session: dict[str, Any]) -> None:
    flask_session.pop(SESSION_KEY, None)
    flask_session.pop(PKCE_KEY, None)
    flask_session.pop(STATE_KEY, None)
    _touch_session(flask_session)


def build_authorize_url(flask_session: dict[str, Any], *, redirect_uri: str, return_to: str = "/") -> str:
    if not oauth_configured():
        raise RuntimeError(
            "Falta SNOW_OAUTH_CLIENT_ID en .env. Pide al admin de ServiceNow una app OAuth "
            "(Application Registry → Authorization Code + PKCE)."
        )
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    flask_session[PKCE_KEY] = verifier
    flask_session[STATE_KEY] = state
    flask_session[RETURN_KEY] = return_to or "/"
    _touch_session(flask_session)

    params = {
        "response_type": "code",
        "client_id": oauth_client_id(),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_snow_instance()}/oauth_auth.do?{urlencode(params)}"


def exchange_code_for_tokens(
    flask_session: dict[str, Any],
    *,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    verifier = (flask_session.get(PKCE_KEY) or "").strip()
    if not verifier:
        raise RuntimeError("OAuth PKCE verifier missing — restart login from GocView.")

    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": oauth_client_id(),
        "code_verifier": verifier,
    }
    secret = oauth_client_secret()
    if secret:
        data["client_secret"] = secret

    r = requests.post(
        f"{_snow_instance()}/oauth_token.do",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ServiceNow token exchange HTTP {r.status_code}: {(r.text or '')[:300]}")
    body = r.json()
    flask_session.pop(PKCE_KEY, None)
    flask_session.pop(STATE_KEY, None)
    _touch_session(flask_session)
    save_token_bundle(flask_session, body)
    return body


def refresh_token_bundle(flask_session: dict[str, Any]) -> bool:
    bundle = flask_session.get(SESSION_KEY) or {}
    refresh = (bundle.get("refresh_token") or "").strip()
    if not refresh:
        return False
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": oauth_client_id(),
    }
    secret = oauth_client_secret()
    if secret:
        data["client_secret"] = secret
    try:
        r = requests.post(
            f"{_snow_instance()}/oauth_token.do",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=_HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return False
        save_token_bundle(flask_session, r.json())
        return True
    except Exception:
        return False


def api_requests_session(flask_session: dict[str, Any]) -> requests.Session | None:
    bundle = get_token_bundle(flask_session)
    if not bundle:
        return None
    token = bundle.get("access_token")
    if not token:
        return None
    sess = requests.Session()
    sess.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
    )
    return sess


def auth_status(flask_session: dict[str, Any]) -> dict[str, Any]:
    from tools.servicenow_browser_connect import auto_connect_available
    from tools.servicenow_session import cookie_session_connected

    oauth_connected = get_token_bundle(flask_session) is not None
    cookie_connected = cookie_session_connected(flask_session)
    connected = oauth_connected or cookie_connected
    oauth_ready = oauth_configured()

    out: dict[str, Any] = {
        "configured": oauth_ready,
        "connected": connected,
        "instance": _snow_instance(),
        "method": "oauth" if oauth_connected else ("cookie" if cookie_connected else None),
        "manual_login": not oauth_ready,
        "auto_connect": auto_connect_available(),
    }
    if connected:
        return out

    if oauth_ready:
        out["login_path"] = "/oauth/snow/login"
        out["message"] = "Conecta ServiceNow con Okta."
    else:
        out["message"] = (
            "Sin acceso a OAuth de ServiceNow: inicia sesión con Okta en otra pestaña "
            "y pega tu cookie glide_session_store aquí."
        )
    return out
