#!/usr/bin/env python3
"""
Escribe credenciales AWS resueltas por el CLI en el .env del repo (sin imprimir secretos).

Flujo típico (SSO):
  aws sso login --profile TU_PERFIL
  python3 scripts/sync_aws_creds_to_dotenv.py --profile TU_PERFIL

Flujo con variables ya exportadas en la shell:
  eval "$(aws configure export-credentials --profile TU_PERFIL --format env)"
  python3 scripts/sync_aws_creds_to_dotenv.py --from-env

Requisitos: AWS CLI v2.13+ (comando ``aws configure export-credentials``) para --profile.
El .env está en .gitignore; no subas claves a git.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DOTENV = _REPO / ".env"

_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION")


def _aws_executable() -> str:
    path = shutil.which("aws")
    if not path:
        raise RuntimeError(
            "No está instalado `aws` (AWS CLI) en el PATH de esta terminal.\n"
            "  • macOS (Homebrew): brew install awscli\n"
            "  • Comprueba: aws --version\n"
            "Ejecuta este script en la misma terminal donde `aws` funcione (p. ej. la de Terminal.app / iTerm)."
        )
    return path


def _parse_env_block(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, _, rest = line.partition("=")
        k = k.strip()
        v = rest.strip().strip('"').strip("'")
        if k in _KEYS:
            out[k] = v
    return out


def _export_credentials_profile(profile: str) -> dict[str, str]:
    aws = _aws_executable()
    r = subprocess.run(
        [aws, "configure", "export-credentials", "--profile", profile, "--format", "env"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(
            f"aws configure export-credentials falló (código {r.returncode}).\n{err}\n"
            "¿Tienes AWS CLI v2.13+? Prueba: aws configure export-credentials --help\n"
            f"Si usas SSO: aws sso login --profile {profile}"
        )
    creds = _parse_env_block(r.stdout)
    if not creds.get("AWS_ACCESS_KEY_ID") or not creds.get("AWS_SECRET_ACCESS_KEY"):
        raise RuntimeError("La salida del CLI no incluye AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.")
    r2 = subprocess.run(
        [aws, "configure", "get", "region", "--profile", profile],
        capture_output=True,
        text=True,
        timeout=15,
    )
    region = (r2.stdout or "").strip() if r2.returncode == 0 else ""
    if region:
        creds["AWS_REGION"] = region
    elif "AWS_REGION" not in creds:
        creds["AWS_REGION"] = "us-east-1"
    if "AWS_SESSION_TOKEN" not in creds:
        creds["AWS_SESSION_TOKEN"] = ""
    return creds


def _from_current_environ() -> dict[str, str]:
    ak = (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    sk = (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    if not ak or not sk:
        raise RuntimeError(
            "Faltan AWS_ACCESS_KEY_ID o AWS_SECRET_ACCESS_KEY en el entorno actual. "
            "Exporta credenciales primero o usa --profile."
        )
    return {
        "AWS_ACCESS_KEY_ID": ak,
        "AWS_SECRET_ACCESS_KEY": sk,
        "AWS_SESSION_TOKEN": (os.environ.get("AWS_SESSION_TOKEN") or "").strip(),
        "AWS_REGION": (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1").strip(),
    }


def _merge_dotenv(path: Path, updates: dict[str, str], dry_run: bool) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"No existe {path}")
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    replaced = {k: False for k in _KEYS}
    new_lines: list[str] = []
    for line in lines:
        m = None
        for k in _KEYS:
            if re.match(rf"^{re.escape(k)}\s*=", line):
                m = k
                break
        if m:
            val = updates.get(m, "")
            new_lines.append(f"{m}={val}")
            replaced[m] = True
        else:
            new_lines.append(line)
    for k in _KEYS:
        if not replaced[k]:
            new_lines.append(f"{k}={updates.get(k, '')}")
    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body += "\n"
    if not dry_run:
        path.write_text(body, encoding="utf-8")
    return body


def _caller_summary(profile: str | None) -> str:
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
    try:
        aws = _aws_executable()
    except RuntimeError as e:
        return str(e)
    r = subprocess.run(
        [aws, "sts", "get-caller-identity", "--output", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if r.returncode != 0:
        return f"(no se pudo verificar identidad: {(r.stderr or r.stdout or '').strip()[:200]})"
    try:
        import json

        d = json.loads(r.stdout)
        return f"Account={d.get('Account')} ARN={d.get('Arn')}"
    except Exception:
        return r.stdout.strip()[:300]


def main() -> int:
    p = argparse.ArgumentParser(description="Sync AWS creds into repo .env")
    p.add_argument("--profile", help="Perfil de ~/.aws/config (tras aws sso login, etc.)")
    p.add_argument(
        "--from-env",
        action="store_true",
        help="Leer AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN / AWS_REGION del proceso actual",
    )
    p.add_argument("--dotenv", type=Path, default=_DEFAULT_DOTENV, help=f"Ruta al .env (default: {_DEFAULT_DOTENV})")
    p.add_argument("--dry-run", action="store_true", help="Mostrar solo resumen; no escribir .env")
    args = p.parse_args()

    if args.from_env and args.profile:
        p.error("Usa solo uno: --profile o --from-env")
    if not args.from_env and not args.profile:
        p.error("Indica --profile NOMBRE o --from-env")

    try:
        if args.from_env:
            updates = _from_current_environ()
            who = _caller_summary(None)
        else:
            updates = _export_credentials_profile(args.profile)
            who = _caller_summary(args.profile)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        _merge_dotenv(args.dotenv, updates, dry_run=args.dry_run)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    mode = "DRY-RUN (no escrito)" if args.dry_run else f"Actualizado {args.dotenv}"
    print(mode)
    print("STS:", who)
    print("AWS_REGION en .env:", updates.get("AWS_REGION", ""))
    print("Token de sesión:", "sí" if updates.get("AWS_SESSION_TOKEN") else "no (clave larga o vacío)")
    print("AWS_ACCESS_KEY_ID (prefijo):", (updates.get("AWS_ACCESS_KEY_ID") or "")[:6] + "…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
