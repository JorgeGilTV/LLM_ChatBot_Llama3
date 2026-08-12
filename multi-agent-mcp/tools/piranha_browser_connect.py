"""Automatic Piranha login via Playwright (local dev — Okta in Chromium)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from tools.piranha_session import (
    _piranha_base,
    _piranha_host,
    save_cookies,
    validate_session,
)

_log = logging.getLogger(__name__)

_CONNECT_LOCK = threading.Lock()
_ACTIVE_CONNECTS: set[str] = set()


def _connect_store_dir() -> Path:
    root = Path(os.getenv("PIRANHA_CONNECT_DIR") or os.path.join(os.getcwd(), "data", "piranha_connect"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save_connect_result(connect_id: str, result: dict[str, Any]) -> None:
    (_connect_store_dir() / f"{connect_id}.json").write_text(json.dumps(result), encoding="utf-8")


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
    flag = (os.getenv("PIRANHA_AUTO_CONNECT") or "").strip().lower()
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


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _piranha_cookies(context) -> dict[str, str]:
    host = _piranha_host()
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
    return any(k.lower().startswith("awselbauthsessioncookie") for k in cookies)


def _wait_for_piranha_session(page, context, base: str, connect_id: str, timeout_sec: int = 300) -> dict[str, str]:
    deadline = time.time() + timeout_sec
    host = _piranha_host()
    last_url = ""

    while time.time() < deadline:
        try:
            url = page.url or ""
            if url != last_url:
                _log.info("Piranha auto-connect %s: url=%s", connect_id, url[:120])
                last_url = url

            cookies = _piranha_cookies(context)
            on_piranha = host in url and "okta.com" not in url.lower()

            if on_piranha and _has_session_cookies(cookies):
                _log.info("Piranha auto-connect %s: session ready", connect_id)
                return cookies

            if on_piranha and not _has_session_cookies(cookies):
                try:
                    page.goto(f"{base}/#employees", wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(2000)
                    cookies = _piranha_cookies(context)
                    if _has_session_cookies(cookies):
                        return cookies
                except Exception as e:
                    _log.debug("Piranha auto-connect %s goto employees: %s", connect_id, e)

            page.wait_for_timeout(1500)
        except Exception as e:
            _log.debug("Piranha auto-connect %s poll: %s", connect_id, e)
            time.sleep(1.5)

    cookies = _piranha_cookies(context)
    if _has_session_cookies(cookies):
        return cookies
    raise RuntimeError("Timed out waiting for Okta login. Complete sign-in in the Chromium window.")


def _run_browser_connect(connect_id: str, base: str) -> None:
    result: dict[str, Any] = {"ok": False}
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(base, wait_until="domcontentloaded", timeout=120_000)
            _log.info("Piranha auto-connect %s: waiting for Okta login…", connect_id)
            cookies = _wait_for_piranha_session(page, context, base, connect_id)
            browser.close()
            browser = None

        if not _has_session_cookies(cookies):
            raise RuntimeError("No ALB session cookies were captured.")

        result = {"ok": True, "cookies": cookies}
        _log.info("Piranha auto-connect %s: success (%d cookies)", connect_id, len(cookies))
    except Exception as e:
        _log.warning("Piranha auto-connect %s failed: %s", connect_id, e)
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
                "Auto-connect is only available in local development. "
                "On gocview.arlocloud.com paste ALB cookies via /api/piranha/session."
            ),
        }
    if not playwright_available():
        return {
            "success": False,
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }

    connect_id = uuid.uuid4().hex
    base = _piranha_base()
    with _CONNECT_LOCK:
        _save_connect_result(connect_id, {"ok": None, "pending": True})
        _ACTIVE_CONNECTS.add(connect_id)

    thread = threading.Thread(
        target=_run_browser_connect,
        args=(connect_id, base),
        name=f"piranha-connect-{connect_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {
        "success": True,
        "connect_id": connect_id,
        "base_url": base,
        "message": "Chromium opened. Sign in with Okta in that window.",
    }


def poll_auto_connect(connect_id: str, flask_session: dict[str, Any]) -> dict[str, Any]:
    with _CONNECT_LOCK:
        result = _load_connect_result(connect_id)

    if result is None:
        return {"status": "unknown", "error": "Connect session not found. Click Connect again."}
    if result.get("pending"):
        return {"status": "pending", "message": "Waiting for Okta login in Chromium…"}

    if not result.get("ok"):
        err = result.get("error") or "Connection failed."
        with _CONNECT_LOCK:
            _delete_connect_result(connect_id)
        return {"status": "error", "error": err}

    cookies = result.get("cookies") or {}
    save_cookies(flask_session, cookies)
    ok, err = validate_session(flask_session)
    with _CONNECT_LOCK:
        _delete_connect_result(connect_id)

    if not ok:
        from tools.piranha_session import clear_cookies

        clear_cookies(flask_session)
        return {"status": "error", "error": err or "Session validation failed."}

    return {"status": "connected", "message": "Piranha connected."}
