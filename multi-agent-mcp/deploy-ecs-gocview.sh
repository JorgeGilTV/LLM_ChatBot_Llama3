#!/usr/bin/env bash
# Build oneview-goc-ai (linux/arm64), push to ECR, register ECS task revision, redeploy gocview.
#
# Defaults: gocview on cluster hackathon (us-west-2, account 765647031920).
#
# Usage:
#   CHANGE_TICKET=SRE-1234 ./deploy-ecs-gocview.sh
#   JIRA_TICKET=GOC-5678 ./deploy-ecs-gocview.sh   # alias
#   IMAGE_TAG=3.2.25-mcp CHANGE_TICKET=SRE-1234 ./deploy-ecs-gocview.sh
#   SKIP_BUILD=1 CHANGE_TICKET=SRE-1234 ./deploy-ecs-gocview.sh
#   REQUIRE_CHANGE_TICKET=0 ./deploy-ecs-gocview.sh   # solo emergencias (sin ticket)
#   # También lee el ticket del último commit: git commit -m "[SRE-1234] descripción"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

AWS_REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID="${ACCOUNT_ID:-765647031920}"
ECR_REPO="${ECR_REPO:-gocview}"
ECS_CLUSTER="${ECS_CLUSTER:-hackathon}"
ECS_SERVICE="${ECS_SERVICE:-gocview-service}"
TASK_FAMILY="${TASK_FAMILY:-gocview}"
IMAGE_NAME="${IMAGE_NAME:-oneview-goc-ai}"
BUILD_PLATFORM="${BUILD_PLATFORM:-linux/arm64}"
SYNC_AWS_CREDS_TO_ECS="${SYNC_AWS_CREDS_TO_ECS:-0}"
SYNC_SECRETS_TO_ECS="${SYNC_SECRETS_TO_ECS:-1}"
TASK_ROLE_ARN="${TASK_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/gocview-task-role}"
# ArloChat MCP (ALB interno us-east-1; legacy)
ARLOCHAT_MCP_URL="${ARLOCHAT_MCP_URL:-http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080}"
MINTMCP_URL="${MINTMCP_URL:-https://app.mintmcp.com/o/arlo/s/arlo/mcp}"
# MCP_SERVER_URL override; si vacío y hay MINTMCP_API_KEY en .env → MintMCP
MCP_SERVER_URL="${MCP_SERVER_URL:-}"

# Change management — ticket Jira en tags ECS (visible en CloudTrail / inventario AWS).
read_change_ticket_from_git() {
  command -v git >/dev/null 2>&1 || return 0
  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  local subject body msg
  subject="$(git -C "$ROOT" log -1 --pretty=%s 2>/dev/null || true)"
  body="$(git -C "$ROOT" log -1 --pretty=%b 2>/dev/null || true)"
  msg="${subject} ${body}"
  if [[ -n "$msg" ]]; then
    echo "$msg" | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1
  fi
}

CHANGE_TICKET="${CHANGE_TICKET:-${JIRA_TICKET:-}}"
if [[ -z "${CHANGE_TICKET// }" ]]; then
  CHANGE_TICKET="$(read_change_ticket_from_git || true)"
  if [[ -n "$CHANGE_TICKET" ]]; then
    echo "==> Change ticket from git commit: ${CHANGE_TICKET}"
  fi
fi
REQUIRE_CHANGE_TICKET="${REQUIRE_CHANGE_TICKET:-1}"
if [[ "$REQUIRE_CHANGE_TICKET" == "1" && -z "${CHANGE_TICKET// }" ]]; then
  echo "Error: Falta ticket Jira. Ejemplo:" >&2
  echo "  CHANGE_TICKET=SRE-1234 ./deploy-ecs-gocview.sh" >&2
  echo "  git commit -m \"[SRE-1234] deploy gocview: ...\"  # y luego ./deploy-ecs-gocview.sh" >&2
  echo "  (o JIRA_TICKET=GOC-5678). Emergencia sin ticket: REQUIRE_CHANGE_TICKET=0" >&2
  exit 1
fi
if [[ -n "$CHANGE_TICKET" && ! "$CHANGE_TICKET" =~ ^[A-Za-z][A-Za-z0-9]+-[0-9]+$ ]]; then
  echo "Warning: CHANGE_TICKET no parece clave Jira (ej. SRE-1234): $CHANGE_TICKET" >&2
fi
CHANGE_TICKET="$(echo "$CHANGE_TICKET" | tr '[:lower:]' '[:upper:]')"
DEPLOYED_VIA="deploy-ecs-gocview.sh"
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  DEPLOYED_VIA="github-actions/deploy-gocview-ecs"
fi
GIT_COMMIT_SHA=""
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
  GIT_COMMIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
fi
ECS_DEPLOY_TAGS=()
if [[ -n "$CHANGE_TICKET" ]]; then
  ECS_DEPLOY_TAGS=(
    "key=ChangeTicket,value=${CHANGE_TICKET}"
    "key=DeployedVia,value=${DEPLOYED_VIA}"
    "key=Application,value=gocview"
  )
  if [[ -n "$GIT_COMMIT_SHA" ]]; then
    ECS_DEPLOY_TAGS+=("key=GitCommit,value=${GIT_COMMIT_SHA}")
  fi
fi

VERSION="$(grep '^VERSION=' docker-build-export.sh | head -1 | cut -d= -f2 | tr -d '"')"
IMAGE_TAG="${IMAGE_TAG:-${VERSION:-latest}}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-900}"
ALB_IDLE_TIMEOUT="${ALB_IDLE_TIMEOUT:-900}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

read_dotenv_value() {
  local key="$1"
  local file="${2:-.env}"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  python3 - "$file" "$key" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    raise SystemExit(0)
for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, v = s.split("=", 1)
    if k.strip() != key:
        continue
    v = v.strip().strip('"').strip("'")
    print(v)
    raise SystemExit(0)
PY
}

if ! command -v aws >/dev/null 2>&1; then
  echo "Error: aws CLI not found." >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq not found." >&2
  exit 1
fi

echo "==> AWS identity (region ${AWS_REGION})"
if [[ -n "$CHANGE_TICKET" ]]; then
  echo "==> Change ticket: ${CHANGE_TICKET}"
fi
aws sts get-caller-identity --region "$AWS_REGION"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker daemon not running. Start Docker Desktop and retry." >&2
    exit 1
  fi
  echo "==> Building ${IMAGE_NAME}:${IMAGE_TAG} (${BUILD_PLATFORM})"
  docker build --platform "${BUILD_PLATFORM}" \
    -t "${IMAGE_NAME}:latest" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    .
fi

echo "==> ECR login"
aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Push ${ECR_URI}:${IMAGE_TAG} (+ latest)"
docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${ECR_URI}:latest"
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:latest"

echo "==> Register task definition ${TASK_FAMILY} with image ${ECR_URI}:${IMAGE_TAG}"
AWS_ENV_JQ='[]'
if [[ "${SYNC_AWS_CREDS_TO_ECS}" == "1" ]]; then
  DOT_AWS_ACCESS_KEY_ID="$(read_dotenv_value AWS_ACCESS_KEY_ID .env || true)"
  DOT_AWS_SECRET_ACCESS_KEY="$(read_dotenv_value AWS_SECRET_ACCESS_KEY .env || true)"
  DOT_AWS_SESSION_TOKEN="$(read_dotenv_value AWS_SESSION_TOKEN .env || true)"
  DOT_AWS_REGION="$(read_dotenv_value AWS_REGION .env || true)"
  if [[ -n "${DOT_AWS_ACCESS_KEY_ID}" && -n "${DOT_AWS_SECRET_ACCESS_KEY}" ]]; then
    AWS_ENV_JQ="$(
      jq -cn \
        --arg ak "${DOT_AWS_ACCESS_KEY_ID}" \
        --arg sk "${DOT_AWS_SECRET_ACCESS_KEY}" \
        --arg st "${DOT_AWS_SESSION_TOKEN}" \
        --arg rg "${DOT_AWS_REGION}" \
        '
        [
          {name:"AWS_ACCESS_KEY_ID", value:$ak},
          {name:"AWS_SECRET_ACCESS_KEY", value:$sk}
        ]
        + (if ($st|length)>0 then [{name:"AWS_SESSION_TOKEN", value:$st}] else [] end)
        + (if ($rg|length)>0 then [{name:"AWS_REGION", value:$rg}] else [] end)
        '
    )"
    echo "==> Injecting AWS creds from .env into ECS task env (SYNC_AWS_CREDS_TO_ECS=1)"
  else
    echo "==> .env AWS creds not found; skipping AWS env injection into ECS task"
  fi
fi

SECRETS_ENV_JQ='[]'
if [[ "${SYNC_SECRETS_TO_ECS}" == "1" && -f .env ]]; then
  SECRETS_ENV_JQ="$(
    python3 - <<'PY'
import json
import os
import re
from pathlib import Path

keys = {
    "BEDROCK_API_KEY", "AWS_REGION", "AWS_SECRETS_MANAGER_SECRET_ID", "DATADOG_API_KEY",
    "DATADOG_APP_KEY", "DATADOG_SITE", "SPLUNK_HOST", "SPLUNK_TOKEN", "SPLUNK_AUTH_MODE",
    "PAGERDUTY_API_TOKEN", "SLACK_BOT_TOKEN", "SLACK_WEBHOOK_URL", "ATLASSIAN_EMAIL",
    "CONFLUENCE_TOKEN", "CONFLUENCE_ATLASSIAN_HOST", "GRAFANA_URL", "GRAFANA_API_KEY",
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "SNOW_USER", "SNOW_PASSWORD", "ADMIN_TOKEN",
    "GUNICORN_TIMEOUT", "ECS_SYNC_SECRETS_ON_SAVE", "ECS_AWS_REGION", "ECS_CLUSTER", "ECS_SERVICE", "TASK_FAMILY", "MCP_SERVER_URL", "MINTMCP_URL", "MINTMCP_API_KEY",
    "AMPLITUDE_API_KEY", "AMPLITUDE_SECRET_KEY", "AMPLITUDE_CHART_URL", "AMPLITUDE_APP_LAUNCH_CHART_URL", "AMPLITUDE_API_BASE",
    "TABLEAU_PROBE_URL", "FIREBASE_PROBE_URL",
    "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_HTTP_PATH",
}
out = {
    "ADMIN_TOKEN": "ONEVIEW",
    "ECS_SYNC_SECRETS_ON_SAVE": "1",
    "ECS_AWS_REGION": os.environ.get("ECS_AWS_REGION", "us-west-2"),
    "MINTMCP_URL": os.environ.get("MINTMCP_URL", "https://app.mintmcp.com/o/arlo/s/arlo/mcp"),
    "ECS_CLUSTER": os.environ.get("ECS_CLUSTER", "hackathon"),
    "ECS_SERVICE": os.environ.get("ECS_SERVICE", "gocview-service"),
    "TASK_FAMILY": os.environ.get("TASK_FAMILY", "gocview"),
    "GUNICORN_TIMEOUT": os.environ.get("GUNICORN_TIMEOUT", "900"),
}
path = Path(".env")
for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, _, v = s.partition("=")
    k = k.strip()
    if k not in keys:
        continue
    v = v.strip().strip('"').strip("'")
    if v:
        out[k] = v
out.setdefault("ADMIN_TOKEN", "ONEVIEW")
print(json.dumps([{"name": k, "value": v} for k, v in sorted(out.items())]))
PY
  )"
  echo "==> Injecting app secrets from .env into ECS task env (SYNC_SECRETS_TO_ECS=1)"
fi

aws ecs describe-task-definition --region "$AWS_REGION" --task-definition "$TASK_FAMILY" \
  --query 'taskDefinition' | \
  jq --arg IMG "${ECR_URI}:${IMAGE_TAG}" --arg GT "$GUNICORN_TIMEOUT" --arg TASK_ROLE "$TASK_ROLE_ARN" \
     --argjson AWSE "$AWS_ENV_JQ" --argjson SECE "$SECRETS_ENV_JQ" \
  'def drop: ["GUNICORN_TIMEOUT","AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN","AWS_REGION",
    "ADMIN_TOKEN","ECS_SYNC_SECRETS_ON_SAVE","ECS_AWS_REGION","ECS_CLUSTER","ECS_SERVICE","TASK_FAMILY","MCP_SERVER_URL","MINTMCP_URL","MINTMCP_API_KEY",
    "BEDROCK_API_KEY","DATADOG_API_KEY","DATADOG_APP_KEY","DATADOG_SITE","SPLUNK_HOST","SPLUNK_TOKEN",
    "SPLUNK_AUTH_MODE","PAGERDUTY_API_TOKEN","SLACK_BOT_TOKEN","SLACK_WEBHOOK_URL","ATLASSIAN_EMAIL",
    "CONFLUENCE_TOKEN","CONFLUENCE_ATLASSIAN_HOST","GRAFANA_URL","GRAFANA_API_KEY","GEMINI_API_KEY",
    "ANTHROPIC_API_KEY","SNOW_USER","SNOW_PASSWORD","AWS_SECRETS_MANAGER_SECRET_ID",
    "AMPLITUDE_API_KEY","AMPLITUDE_SECRET_KEY","AMPLITUDE_CHART_URL","AMPLITUDE_APP_LAUNCH_CHART_URL","AMPLITUDE_API_BASE",
    "TABLEAU_PROBE_URL","FIREBASE_PROBE_URL",
    "DATABRICKS_HOST","DATABRICKS_TOKEN","DATABRICKS_HTTP_PATH"];
   del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)
   | .taskRoleArn = $TASK_ROLE
   | .containerDefinitions[0].image = $IMG
   | .containerDefinitions[0].environment = (
      ((.containerDefinitions[0].environment // []) | map(select(.name as $n | (drop | index($n)) == null)))
      + [{name: "GUNICORN_TIMEOUT", value: $GT}]
      + $AWSE + $SECE
     )' \
  > /tmp/gocview-taskdef.json

TG_ARN="$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
  --query 'services[0].loadBalancers[0].targetGroupArn' --output text 2>/dev/null || true)"
if [[ -n "$TG_ARN" && "$TG_ARN" != "None" ]]; then
  LB_ARN="$(aws elbv2 describe-target-groups --region "$AWS_REGION" --target-group-arns "$TG_ARN" \
    --query 'TargetGroups[0].LoadBalancerArns[0]' --output text 2>/dev/null || true)"
  if [[ -n "$LB_ARN" && "$LB_ARN" != "None" ]]; then
    CUR_IDLE="$(aws elbv2 describe-load-balancer-attributes --region "$AWS_REGION" --load-balancer-arn "$LB_ARN" \
      --query 'Attributes[?Key==`idle_timeout.timeout_seconds`].Value' --output text 2>/dev/null || true)"
    if [[ -n "$CUR_IDLE" && "$CUR_IDLE" != "$ALB_IDLE_TIMEOUT" ]]; then
      echo "==> ALB idle timeout ${CUR_IDLE}s → ${ALB_IDLE_TIMEOUT}s"
      aws elbv2 modify-load-balancer-attributes --region "$AWS_REGION" --load-balancer-arn "$LB_ARN" \
        --attributes "Key=idle_timeout.timeout_seconds,Value=${ALB_IDLE_TIMEOUT}"
    elif [[ "$CUR_IDLE" == "$ALB_IDLE_TIMEOUT" ]]; then
      echo "==> ALB idle timeout already ${ALB_IDLE_TIMEOUT}s"
    fi
  fi
fi

NEW_REV="$(aws ecs register-task-definition --region "$AWS_REGION" \
  --cli-input-json file:///tmp/gocview-taskdef.json \
  ${ECS_DEPLOY_TAGS:+--tags "${ECS_DEPLOY_TAGS[@]}"} \
  --query 'taskDefinition.revision' --output text)"
echo "    New revision: ${TASK_FAMILY}:${NEW_REV}"
if [[ -n "$CHANGE_TICKET" ]]; then
  TD_ARN="$(aws ecs describe-task-definition --region "$AWS_REGION" \
    --task-definition "${TASK_FAMILY}:${NEW_REV}" \
    --query 'taskDefinition.taskDefinitionArn' --output text)"
  echo "    Tagged: ChangeTicket=${CHANGE_TICKET} on ${TD_ARN}"
fi

echo "==> ECS update-service ${ECS_SERVICE} on ${ECS_CLUSTER}"
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --task-definition "${TASK_FAMILY}:${NEW_REV}" \
  --force-new-deployment \
  ${CHANGE_TICKET:+--propagate-tags TASK_DEFINITION} \
  --query 'service.{serviceName:serviceName,taskDefinition:taskDefinition,desiredCount:desiredCount}' \
  --output table

if [[ -n "$CHANGE_TICKET" && ${#ECS_DEPLOY_TAGS[@]} -gt 0 ]]; then
  SERVICE_ARN="$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
    --query 'services[0].serviceArn' --output text)"
  if [[ -n "$SERVICE_ARN" && "$SERVICE_ARN" != "None" ]]; then
    aws ecs tag-resource --region "$AWS_REGION" \
      --resource-arn "$SERVICE_ARN" \
      --tags "${ECS_DEPLOY_TAGS[@]}" >/dev/null
    echo "==> Tagged ECS service with ChangeTicket=${CHANGE_TICKET}"
  fi
fi

echo "==> Waiting for service stable (up to ~10 min)..."
aws ecs wait services-stable --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

echo "==> Deployments"
aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
  --query 'services[0].deployments[*].{status:status,taskDef:taskDefinition,running:runningCount,desired:desiredCount,pending:pendingCount}' \
  --output table

echo "Done. Image: ${ECR_URI}:${IMAGE_TAG}"
if [[ -n "$CHANGE_TICKET" ]]; then
  echo "Change ticket: ${CHANGE_TICKET} (tags en task definition y servicio ECS → CloudTrail)"
fi