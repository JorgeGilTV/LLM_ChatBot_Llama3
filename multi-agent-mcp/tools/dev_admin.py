"""
Operaciones de administración local / contenedor: git pull y sustitución de .env.

Requieren cabecera ``X-Admin-Token`` igual a la variable de entorno ``ADMIN_TOKEN``.
Sin ``ADMIN_TOKEN`` definido, los endpoints responden 403 (deshabilitados).

Nota Docker: la imagen por defecto excluye ``.git`` (``.dockerignore``) y no instala ``git``;
``git pull`` solo funcionará si montas el repo con historial git o añades git + .git al contexto.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_ENV_UPLOAD_BYTES = 512 * 1024


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def admin_token_configured() -> bool:
    return bool((os.environ.get("ADMIN_TOKEN") or "").strip())


def verify_admin_request_token(request_token: str | None) -> tuple[bool, str]:
    expected = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if not expected:
        return False, "ADMIN_TOKEN no está definido en el servidor; las acciones de admin están deshabilitadas."
    got = (request_token or "").strip()
    if not got or got != expected:
        return False, "Token inválido o ausente (cabecera X-Admin-Token)."
    return True, ""


def git_update_status_and_pull(root: Path | None = None) -> dict[str, Any]:
    root = root or app_root()
    git_bin = shutil.which("git")
    if not git_bin:
        return {
            "success": False,
            "error": "El ejecutable `git` no está instalado en este entorno (típico en imagen slim). "
            "Instala git en la imagen o monta el repo con `.git` desde el host.",
        }

    def run_git(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git_bin, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    inside = run_git(["rev-parse", "--is-inside-work-tree"], timeout=15)
    if inside.returncode != 0 or "true" not in (inside.stdout or "").strip().lower():
        return {
            "success": False,
            "error": "No hay repositorio git en el directorio de la app (p. ej. imagen Docker sin `.git`). "
            "Monta el código con el directorio `.git` o despliega una nueva imagen desde CI.",
            "stdout": (inside.stdout or "")[:500],
            "stderr": (inside.stderr or "")[:500],
        }

    head_before = run_git(["rev-parse", "HEAD"], timeout=15)
    if head_before.returncode != 0:
        return {"success": False, "error": "No se pudo leer HEAD", "stderr": head_before.stderr[:800]}
    before = head_before.stdout.strip()

    fetch = run_git(["fetch", "origin"], timeout=240)
    if fetch.returncode != 0:
        return {
            "success": False,
            "error": "git fetch falló",
            "stderr": (fetch.stderr or fetch.stdout or "")[:4000],
        }

    status = run_git(["status", "-sb"], timeout=30)
    pull = run_git(["pull", "--ff-only"], timeout=240)
    head_after = run_git(["rev-parse", "HEAD"], timeout=15)
    after = head_after.stdout.strip() if head_after.returncode == 0 else before

    ok = pull.returncode == 0
    out = {
        "success": ok,
        "commit_before": before,
        "commit_after": after,
        "already_up_to_date": ok and before == after,
        "status_short": (status.stdout or "").strip()[:2000],
        "pull_stdout": (pull.stdout or "").strip()[:4000],
        "pull_stderr": (pull.stderr or "").strip()[:4000],
    }
    if ok and before != after:
        out["restart_hint"] = "Cambió el commit: reinicia el proceso o el task ECS para cargar código Python nuevo."
    if ok and before == after:
        out["message"] = "Ya estabas en el último commit (fast-forward no necesario)."
    if not ok:
        out["error"] = "git pull --ff-only falló (¿rama divergente? resuelve en el host y vuelve a intentar)."
    return out


def _env_upload_name_ok(name: str) -> bool:
    """macOS/Finder often hides dotfiles; accept env.txt, env, *.env, etc."""
    _allowed = {".env", "env", ".env.local", ".env.production", "env.txt", "dotenv.txt"}
    _lower = name.lower()
    return (
        _lower in _allowed
        or _lower.endswith(".env")
        or _lower.endswith(".env.txt")
    )


def _write_dotenv_text(text: str, root: Path, uploaded_as: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    if len(raw) > MAX_ENV_UPLOAD_BYTES:
        return {"success": False, "error": f"El contenido supera {MAX_ENV_UPLOAD_BYTES} bytes."}
    dest = root / ".env"
    backup = root / f".env.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    if dest.is_file():
        try:
            shutil.copy2(dest, backup)
        except OSError:
            backup = None
    dest.write_text(text, encoding="utf-8")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return {
        "success": True,
        "path": str(dest),
        "uploaded_as": uploaded_as,
        "bytes": len(raw),
        "lines": lines,
        "backup": str(backup) if backup else None,
        "restart_required": True,
        "hint": "Reinicia el proceso (o el contenedor) para que la app vuelva a leer todas las variables.",
    }


def save_dotenv_text_content(text: str, root: Path | None = None) -> dict[str, Any]:
    """Guarda texto pegado como ``<root>/.env``."""
    root = root or app_root()
    if text is None or not str(text).strip():
        return {"success": False, "error": "El contenido está vacío."}
    return _write_dotenv_text(str(text), root, "paste")


def save_uploaded_dotenv(file_storage, root: Path | None = None) -> dict[str, Any]:
    """Guarda el cuerpo subido como ``<root>/.env``. ``file_storage`` es un FileStorage de Werkzeug."""
    root = root or app_root()
    if file_storage is None or not getattr(file_storage, "filename", None):
        return {"success": False, "error": "Falta el archivo (campo de formulario `file`)."}
    name = os.path.basename(str(file_storage.filename or "").strip())
    if not name or name in (".", ".."):
        return {"success": False, "error": "Nombre de archivo no válido."}
    if not _env_upload_name_ok(name):
        return {
            "success": False,
            "error": (
                "Nombre no reconocido. Usa .env, env, env.txt o pega el contenido en la caja de texto."
            ),
        }
    raw = file_storage.read(MAX_ENV_UPLOAD_BYTES + 1)
    if len(raw) > MAX_ENV_UPLOAD_BYTES:
        return {"success": False, "error": f"El archivo supera {MAX_ENV_UPLOAD_BYTES} bytes."}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": "El archivo debe ser UTF-8 válido."}
    return _write_dotenv_text(text, root, name)
