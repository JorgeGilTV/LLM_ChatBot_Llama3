"""
Optional bootstrap: load key/value pairs from AWS Secrets Manager into os.environ.

Enable by setting AWS_SECRETS_MANAGER_SECRET_ID (secret name or ARN). The secret
string should be a JSON object whose keys are environment variable names, e.g.:
  {"SLACK_WEBHOOK_URL":"https://...","BEDROCK_API_KEY":"ABSK..."}

IAM: secretsmanager:GetSecretValue on that secret (and kms:Decrypt if using a CMK).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_ENV_KEY_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def load_aws_secrets_manager_into_environ() -> bool:
    """
    Fetch one secret from AWS Secrets Manager and merge into os.environ.

    Returns True if a secret was loaded, False if disabled or empty.
    Raises if AWS_SECRETS_MANAGER_REQUIRED is set and loading fails.
    """
    secret_id = (
        os.getenv("AWS_SECRETS_MANAGER_SECRET_ID")
        or os.getenv("AWS_SECRETS_MANAGER_SECRET_ARN")
        or ""
    ).strip()
    if not secret_id:
        return False

    region = (
        os.getenv("AWS_SECRETS_MANAGER_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1"
    ).strip()

    overwrite = _truthy("AWS_SECRETS_MANAGER_OVERWRITE")
    required = _truthy("AWS_SECRETS_MANAGER_REQUIRED")

    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_id)
    except ClientError as e:
        msg = f"AWS Secrets Manager error: {e}"
        logger.warning("%s", msg)
        if required:
            raise RuntimeError(msg) from e
        return False
    except Exception as e:
        msg = f"AWS Secrets Manager: {e}"
        logger.warning("%s", msg)
        if required:
            raise RuntimeError(msg) from e
        return False

    if "SecretString" not in resp or not resp["SecretString"]:
        msg = "Secret has no SecretString (use a text secret with JSON body)"
        logger.warning(msg)
        if required:
            raise RuntimeError(msg)
        return False

    raw = resp["SecretString"].strip()
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = "SecretString must be valid JSON (object with env var names as keys)"
        logger.warning("%s: %s", msg, e)
        if required:
            raise RuntimeError(msg) from e
        return False

    if not isinstance(data, dict):
        msg = "Secret JSON root must be an object, not array/string"
        logger.warning(msg)
        if required:
            raise RuntimeError(msg)
        return False

    n = 0
    for key, val in data.items():
        if not isinstance(key, str) or not _ENV_KEY_OK.match(key):
            continue
        if val is None:
            continue
        if not isinstance(val, (str, int, float, bool)):
            logger.debug("Skipping non-scalar key %s", key)
            continue
        sval = str(val).strip() if isinstance(val, str) else str(val)
        if key in os.environ and not overwrite:
            continue
        os.environ[key] = sval
        n += 1

    logger.info(
        "Loaded %d environment variable(s) from AWS Secrets Manager (%s)",
        n,
        secret_id,
    )
    return True
