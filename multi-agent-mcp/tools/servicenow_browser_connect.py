"""Automatic ServiceNow login via Playwright (local dev — opens browser for Okta)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.servicenow_session import save_session_auth, validate_session

_log = logging.getLogger(__name__)

DEFAULT_SNOW_INSTANCE = "https://arlo.service-now.com"
_CONNECT_LOCK = threading.Lock()
_ACTIVE_CONNECTS: set[str] = set()


def _connect_store_dir() -> Path:
    root = Path(os.getenv("SNOW_CONNECT_DIR") or os.path.join(os.getcwd(), "data", "snow_connect"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save_connect_result(connect_id: str, result: dict[str, Any]) -> None:
    path = _connect_store_dir() / f"{connect_id}.json"
    path.write_text(json.dumps(result), encoding="utf-8")


def _load_connect_result(connect_id: str) -> dict[str, Any] | None:
    path = _connect_store_dir() / f"{connect_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _delete_connect_result(connect_id: str) -> None:
    try:
        (_connect_store_dir() / f"{connect_id}.json").unlink(missing_ok=True)
    except Exception:
        pass


def auto_connect_available() -> bool:
    """Playwright auto-connect only works on a developer machine (not ECS/production)."""
    flag = (os.getenv("SNOW_AUTO_CONNECT") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return playwright_available()
    public = (os.getenv("GOCVIEW_PUBLIC_URL") or "").lower()
    if public and ("arlocloud.com" in public or "gocview." in public):
        return False
    if os.getenv("FLASK_ENV") == "production" and not flag:
        return False
    return playwright_available()

_GCK_JS = """
() => {
  if (typeof window.g_ck === 'string' && window.g_ck.length > 5) return window.g_ck;
  if (window.NOW && typeof window.NOW.g_ck === 'string') return window.NOW.g_ck;
  const meta = document.querySelector('meta[name="g_ck"]');
  if (meta && meta.content) return meta.content;
  if (window.GlideSession && typeof window.GlideSession.getSessionToken === 'function') {
    try { return window.GlideSession.getSessionToken() || ''; } catch (e) {}
  }
  return '';
}
"""


def _snow_instance() -> str:
    return (os.getenv("SNOW_INSTANCE") or DEFAULT_SNOW_INSTANCE).rstrip("/")


def _snow_host() -> str:
    return urlparse(_snow_instance()).hostname or "arlo.service-now.com"


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _sn_cookies(context) -> dict[str, str]:
    host = _snow_host()
    out: dict[str, str] = {}
    for c in context.cookies():
        name = c.get("name")
        value = c.get("value")
        domain = (c.get("domain") or "").lstrip(".")
        if not name or not value:
            continue
        if host in domain or domain in host:
            out[name] = value
    return out


def _has_session_cookies(cookies: dict[str, str]) -> bool:
    keys = {k.lower() for k in cookies}
    return bool(keys & {"glide_session_store", "jsessionid"})


def _wait_for_sn_session(page, context, instance: str, connect_id: str, timeout_sec: int = 300) -> tuple[str, dict[str, str]]:
    """Poll until ServiceNow session cookies + g_ck are present (survives Okta redirects)."""
    deadline = time.time() + timeout_sec
    last_url = ""
    navigated_home = False

    while time.time() < deadline:
        try:
            url = page.url or ""
            if url != last_url:
                _log.info("ServiceNow auto-connect %s: url=%s", connect_id, url[:120])
                last_url = url

            on_sn = _snow_host() in url
            cookies = _sn_cookies(context)

            if on_sn and _has_session_cookies(cookies) and not navigated_home:
                # Classic landing page reliably exposes g_ck
                try:
                    page.goto(f"{instance}/navpage.do", wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(2000)
                    navigated_home = True
                except Exception as e:
                    _log.warning("ServiceNow auto-connect %s: navpage goto: %s", connect_id, e)
                    navigated_home = True

            if on_sn and _has_session_cookies(cookies):
                g_ck = ""
                try:
                    g_ck = page.evaluate(_GCK_JS) or ""
                except Exception:
                    g_ck = ""
                if g_ck and len(g_ck) > 5:
                    _log.info("ServiceNow auto-connect %s: session ready", connect_id)
                    return str(g_ck), cookies

            page.wait_for_timeout(1500)
        except Exception as e:
            _log.debug("ServiceNow auto-connect %s poll: %s", connect_id, e)
            time.sleep(1.5)

    cookies = _sn_cookies(context)
    if _has_session_cookies(cookies):
        raise RuntimeError(
            "Login detectado pero no se obtuvo g_ck. Cierra Chromium e intenta modo manual "
            "(pega cookies + window.g_ck)."
        )
    raise RuntimeError(
        "Tiempo agotado esperando login. ¿Completaste Okta en la ventana de Chromium?"
    )


def _run_browser_connect(connect_id: str, instance: str) -> None:
    result: dict[str, Any] = {"ok": False}
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(instance, wait_until="domcontentloaded", timeout=120_000)
            _log.info("ServiceNow auto-connect %s: waiting for Okta login…", connect_id)

            g_ck, cookies = _wait_for_sn_session(page, context, instance, connect_id)
            browser.close()
            browser = None

        if not g_ck:
            raise RuntimeError("No se obtuvo g_ck tras el login.")
        if not _has_session_cookies(cookies):
            raise RuntimeError("No se capturaron cookies de sesión.")

        result = {"ok": True, "cookies": cookies, "user_token": str(g_ck)}
        _log.info("ServiceNow auto-connect %s: success (%d cookies)", connect_id, len(cookies))
    except Exception as e:
        _log.warning("ServiceNow auto-connect %s failed: %s", connect_id, e)
        result = {"ok": False, "error": str(e)}
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        with _CONNECT_LOCK:
            _save_connect_result(connect_id, result)
            _ACTIVE_CONNECTS.discard(connect_id)


def start_auto_connect() -> dict[str, Any]:
    if not auto_connect_available():
        return {
            "success": False,
            "error": (
                "La conexión automática solo está disponible en desarrollo local. "
                "En gocview.arlocloud.com usa el modo manual (pegar cookies) abajo."
            ),
        }
    if not playwright_available():
        return {
            "success": False,
            "error": (
                "Playwright no está instalado. Ejecuta: "
                "pip install playwright && playwright install chromium"
            ),
        }

    connect_id = uuid.uuid4().hex
    instance = _snow_instance()
    with _CONNECT_LOCK:
        _save_connect_result(connect_id, {"ok": None, "pending": True})
        _ACTIVE_CONNECTS.add(connect_id)

    thread = threading.Thread(
        target=_run_browser_connect,
        args=(connect_id, instance),
        name=f"snow-connect-{connect_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {
        "success": True,
        "connect_id": connect_id,
        "instance": instance,
        "message": "Se abrió Chromium. Inicia sesión con Okta en esa ventana.",
    }


def poll_auto_connect(connect_id: str, flask_session: dict[str, Any]) -> dict[str, Any]:
    with _CONNECT_LOCK:
        result = _load_connect_result(connect_id)

    if result is None:
        return {"status": "unknown", "error": "Sesión de conexión no encontrada. Pulsa Conectar de nuevo."}
    if result.get("pending"):
        return {"status": "pending", "message": "Esperando login en Chromium… (no cierres la ventana)"}

    if not result.get("ok"):
        err = result.get("error") or "Conexión fallida."
        with _CONNECT_LOCK:
            _delete_connect_result(connect_id)
        return {"status": "error", "error": err}

    cookies = result.get("cookies") or {}
    user_token = str(result.get("user_token") or "")
    save_session_auth(flask_session, cookies, user_token=user_token)
    ok, err = validate_session(flask_session)
    with _CONNECT_LOCK:
        _delete_connect_result(connect_id)

    if not ok:
        from tools.servicenow_session import clear_cookies

        clear_cookies(flask_session)
        return {"status": "error", "error": err or "Validación de sesión fallida."}

    return {"status": "connected", "message": "ServiceNow conectado."}


def cancel_auto_connect(connect_id: str) -> None:
    with _CONNECT_LOCK:
        _delete_connect_result(connect_id)
        _ACTIVE_CONNECTS.discard(connect_id)
