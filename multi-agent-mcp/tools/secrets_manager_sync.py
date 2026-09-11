"""Create or update a JSON secret in AWS Secrets Manager from a .env file.

The app already loads that JSON into os.environ at startup
(``tools/aws_secrets_env.py``). Static AWS keys are never stored in the secret:
the ECS task role is used instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_KEY_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DEFAULT_SECRET_ID = "oneview/goc/prod"
DEFAULT_REGION = "us-west-2"

SKIP_KEYS = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECRETS_MANAGER_SECRET_ID",
        "AWS_SECRETS_MANAGER_SECRET_ARN",
        "AWS_SECRETS_MANAGER_REGION",
        "AWS_SECRETS_MANAGER_OVERWRITE",
        "AWS_SECRETS_MANAGER_REQUIRED",
    }
)


def parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        raise FileNotFoundError(f"No existe {path}")
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, rest = line.partition("=")
        key = key.strip()
        if not _ENV_KEY_OK.match(key) or key in SKIP_KEYS:
            continue
        val = rest.strip().strip('"').strip("'")
        if val:
            out[key] = val
    return out


def _client(region: str):
    import boto3

    saved: dict[str, str] = {}
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        val = os.environ.pop(key, None)
        if val:
            saved[key] = val
    try:
        return boto3.client("secretsmanager", region_name=region)
    finally:
        os.environ.update(saved)


def get_secret_json(secret_id: str, region: str) -> dict[str, str] | None:
    from botocore.exceptions import ClientError

    client = _client(region)
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in {"ResourceNotFoundException", "ResourceNotFound"}:
            return None
        raise
    raw = (resp.get("SecretString") or "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("El secreto debe ser un objeto JSON")
    return {str(k): str(v) for k, v in data.items() if v is not None}


def put_secret_json(
    secret_id: str,
    region: str,
    payload: dict[str, str],
    *,
    merge: bool = True,
    description: str = "OneView GOC app secrets (JSON env map)",
) -> dict[str, Any]:
    """Create or update the secret. Never logs values."""
    from botocore.exceptions import ClientError

    client = _client(region)
    current = get_secret_json(secret_id, region)
    created = current is None
    merged = dict(current or {})
    if merge:
        merged.update(payload)
    else:
        merged = dict(payload)

    body = json.dumps(merged, sort_keys=True)
    if created:
        try:
            resp = client.create_secret(
                Name=secret_id,
                Description=description,
                SecretString=body,
            )
        except ClientError:
            raise
        arn = resp.get("ARN", "")
        action = "created"
    else:
        resp = client.put_secret_value(SecretId=secret_id, SecretString=body)
        arn = resp.get("ARN", "")
        action = "updated"

    return {
        "action": action,
        "secret_id": secret_id,
        "region": region,
        "arn": arn,
        "key_count": len(merged),
        "keys": sorted(merged.keys()),
    }
