#!/usr/bin/env python3
"""
Copia el .env local a un secreto JSON de AWS Secrets Manager (sin imprimir valores).

  python3 scripts/push_env_to_secrets_manager.py --dry-run
  python3 scripts/push_env_to_secrets_manager.py --push

Por defecto: secret id oneview/goc/prod, región us-west-2 (ECS gocview).
No sube AWS_ACCESS_KEY_ID / SECRET / SESSION_TOKEN: la tarea usa el rol IAM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from tools.secrets_manager_sync import (  # noqa: E402
    DEFAULT_REGION,
    DEFAULT_SECRET_ID,
    parse_dotenv,
    put_secret_json,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Push .env keys into AWS Secrets Manager JSON")
    p.add_argument("--env-file", type=Path, default=_REPO / ".env")
    p.add_argument("--secret-id", default=DEFAULT_SECRET_ID)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--push", action="store_true", help="Crear o actualizar el secreto")
    p.add_argument("--dry-run", action="store_true", help="Solo listar nombres de claves (default si no hay --push)")
    p.add_argument("--replace", action="store_true", help="Sustituir el JSON entero (sin merge)")
    args = p.parse_args()
    if not args.push:
        args.dry_run = True

    try:
        payload = parse_dotenv(args.env_file)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Archivo: {args.env_file}")
    print(f"Secret:  {args.secret_id} ({args.region})")
    print(f"Claves:  {len(payload)}")
    print(" ".join(sorted(payload.keys())))

    if args.dry_run and not args.push:
        print("DRY-RUN: no se escribió nada. Añade --push para crear/actualizar.")
        return 0

    try:
        result = put_secret_json(
            args.secret_id,
            args.region,
            payload,
            merge=not args.replace,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"{result['action']}: {result['secret_id']}  keys={result['key_count']}")
    if result.get("arn"):
        print("ARN:", result["arn"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
