"""
Push secret env vars from the running app into the ECS task definition and redeploy.
Requires AWS credentials + ECS_CLUSTER / ECS_SERVICE / TASK_FAMILY in the environment.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from tools.env_secrets import ALL_SECRET_KEYS, _read_env_values

logger = logging.getLogger(__name__)

ECS_ENV_KEYS: frozenset[str] = ALL_SECRET_KEYS | frozenset(
    {
        "GUNICORN_TIMEOUT",
        "ADMIN_TOKEN",
        "ECS_SYNC_SECRETS_ON_SAVE",
        "ECS_AWS_REGION",
        "ECS_CLUSTER",
        "ECS_SERVICE",
        "TASK_FAMILY",
        "MCP_SERVER_URL",
        "MINTMCP_URL",
        "MINTMCP_API_KEY",
    }
)


def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _ecs_region() -> str:
    # ECS cluster is always us-west-2; do not use AWS_REGION (Bedrock / other services).
    return (os.getenv("ECS_AWS_REGION") or "us-west-2").strip()


def _ecs_boto_client():
    """Use the ECS task role; ignore stale static AWS_* keys injected in task env."""
    import boto3

    saved: dict[str, str] = {}
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        val = os.environ.pop(key, None)
        if val:
            saved[key] = val
    try:
        return boto3.client("ecs", region_name=_ecs_region())
    finally:
        os.environ.update(saved)


def _collect_env_for_task() -> dict[str, str]:
    merged = dict(_read_env_values())
    for key in ECS_ENV_KEYS:
        val = (os.getenv(key) or "").strip()
        if val:
            merged[key] = val
    merged.setdefault("ADMIN_TOKEN", "ONEVIEW")
    merged.setdefault("ECS_AWS_REGION", os.getenv("ECS_AWS_REGION") or "us-west-2")
    merged.setdefault("GUNICORN_TIMEOUT", os.getenv("GUNICORN_TIMEOUT") or "900")
    return {k: v for k, v in merged.items() if k in ECS_ENV_KEYS and v}


def sync_task_env_and_redeploy() -> dict[str, Any]:
    """Register new task revision with merged env and force ECS deployment."""
    if not _truthy("ECS_SYNC_SECRETS_ON_SAVE"):
        return {"ecs_sync": "skipped", "reason": "ECS_SYNC_SECRETS_ON_SAVE no está activo"}

    cluster = (os.getenv("ECS_CLUSTER") or "hackathon").strip()
    service = (os.getenv("ECS_SERVICE") or "gocview-service").strip()
    family = (os.getenv("TASK_FAMILY") or "gocview").strip()
    region = _ecs_region()

    env_map = _collect_env_for_task()
    if not env_map:
        return {"ecs_sync": "skipped", "reason": "No hay variables para sincronizar"}

    try:
        import boto3
    except ImportError:
        return {"ecs_sync": "error", "error": "boto3 no disponible"}

    try:
        ecs = _ecs_boto_client()
        raw = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]
        for drop in (
            "taskDefinitionArn",
            "revision",
            "status",
            "requiresAttributes",
            "compatibilities",
            "registeredAt",
            "registeredBy",
        ):
            raw.pop(drop, None)

        container = raw["containerDefinitions"][0]
        keep = [e for e in (container.get("environment") or []) if e.get("name") not in ECS_ENV_KEYS]
        keep.extend({"name": k, "value": v} for k, v in sorted(env_map.items()))
        container["environment"] = keep

        reg = ecs.register_task_definition(**raw)
        new_arn = reg["taskDefinition"]["taskDefinitionArn"]
        rev = reg["taskDefinition"]["revision"]

        upd = ecs.update_service(
            cluster=cluster,
            service=service,
            taskDefinition=new_arn,
            forceNewDeployment=True,
        )
        return {
            "ecs_sync": "ok",
            "cluster": cluster,
            "service": service,
            "task_definition": f"{family}:{rev}",
            "env_keys": sorted(env_map.keys()),
            "deployment": upd["service"].get("taskDefinition"),
        }
    except Exception as e:
        logger.exception("ecs_secrets_sync failed")
        return {"ecs_sync": "error", "error": f"{type(e).__name__}: {e}"}
