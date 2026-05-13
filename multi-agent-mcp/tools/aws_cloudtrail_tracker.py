"""
AWS CloudTrail — lightweight lookup for the standalone Change Tracker UI.

Uses ``lookup_events`` (one lookup attribute per request, per AWS API).
Requires IAM permission ``cloudtrail:LookupEvents`` on the trail region.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# (value sent to API / filter, label in UI)
CLOUDTRAIL_RESOURCE_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("AWS::S3::Bucket", "S3 Bucket"),
    ("AWS::EC2::Instance", "EC2 Instance"),
    ("AWS::Lambda::Function", "Lambda function"),
    ("AWS::IAM::User", "IAM User"),
    ("AWS::IAM::Role", "IAM Role"),
    ("AWS::RDS::DBInstance", "RDS DB instance"),
    ("AWS::DynamoDB::Table", "DynamoDB table"),
    ("AWS::ECS::Service", "ECS service"),
    ("AWS::EKS::Cluster", "EKS cluster"),
    ("AWS::SecretsManager::Secret", "Secrets Manager secret"),
    ("AWS::KMS::Key", "KMS key"),
    ("AWS::CloudFormation::Stack", "CloudFormation stack"),
    ("AWS::SNS::Topic", "SNS topic"),
    ("AWS::SQS::Queue", "SQS queue"),
    ("AWS::Logs::LogGroup", "CloudWatch Logs group"),
    ("AWS::ElasticLoadBalancingV2::LoadBalancer", "ELB v2 (ALB/NLB)"),
    ("OTHER", "Other / filter after fetch"),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_event_detail(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _event_account_id(detail: dict[str, Any]) -> str:
    rid = detail.get("recipientAccountId")
    if rid:
        return str(rid)
    ui = detail.get("userIdentity") or {}
    return str(ui.get("accountId") or "")


def _event_matches_resource_type(detail: dict[str, Any], resource_type: str) -> bool:
    if not resource_type or resource_type == "OTHER":
        return True
    for r in detail.get("resources") or []:
        if not isinstance(r, dict):
            continue
        if r.get("type") == resource_type:
            return True
    return False


def _serialize_api_event(ev: dict[str, Any]) -> dict[str, Any]:
    detail = _parse_event_detail(ev.get("CloudTrailEvent"))
    un = ev.get("Username") or (detail.get("userIdentity") or {}).get("userName") or ""
    et = ev.get("EventTime")
    if et is not None and hasattr(et, "isoformat"):
        ets = et.isoformat()
    else:
        ets = str(et or "")
    return {
        "EventId": ev.get("EventId", ""),
        "EventTime": ets,
        "EventName": ev.get("EventName", ""),
        "Username": un or "—",
        "ReadOnly": str(ev.get("ReadOnly", "")),
        "Resources": ev.get("Resources") or [],
        "CloudTrailEvent": ev.get("CloudTrailEvent") if isinstance(ev.get("CloudTrailEvent"), str) else json.dumps(
            detail or {}
        )[:8000],
        "recipientAccountId": _event_account_id(detail),
    }


def cloudtrail_search(
    resource_name: str,
    resource_type: str,
    region: str,
    account_id: str,
    lookback_days: int,
    max_events: int,
) -> dict[str, Any]:
    """
    Query CloudTrail in ``region`` for ``ResourceName`` in the time window.
    Filters by ``account_id`` and ``resource_type`` (unless OTHER) client-side.
    """
    resource_name = (resource_name or "").strip()
    if not resource_name:
        return {"success": False, "error": "Resource name or ID is required."}

    region = (region or "").strip().lower()
    if not (6 <= len(region) <= 32 and re.match(r"^[a-z0-9-]+$", region)):
        return {"success": False, "error": "Invalid AWS region (e.g. us-east-1, eu-west-1)."}

    acct = (account_id or "").strip()
    if not re.match(r"^\d{12}$", acct):
        return {"success": False, "error": "AWS account ID must be exactly 12 digits."}

    try:
        lookback = max(1, min(int(lookback_days or 7), 90))
    except (TypeError, ValueError):
        lookback = 7

    try:
        raw_max = int(max_events) if max_events is not None else 50
    except (TypeError, ValueError):
        raw_max = 50
    if raw_max <= 0:
        cap = 10000
    else:
        cap = max(1, min(raw_max, 10000))

    end = _utc_now()
    start = end - timedelta(days=lookback)

    client = boto3.client("cloudtrail", region_name=region)
    collected: list[dict[str, Any]] = []
    next_token = None

    try:
        while len(collected) < cap:
            batch = min(50, cap - len(collected))
            kwargs: dict[str, Any] = {
                "LookupAttributes": [
                    {"AttributeKey": "ResourceName", "AttributeValue": resource_name},
                ],
                "StartTime": start,
                "EndTime": end,
                "MaxResults": batch,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.lookup_events(**kwargs)
            for ev in resp.get("Events") or []:
                detail = _parse_event_detail(ev.get("CloudTrailEvent"))
                if _event_account_id(detail) != acct:
                    continue
                if not _event_matches_resource_type(detail, (resource_type or "").strip()):
                    continue
                collected.append(_serialize_api_event(ev))
                if len(collected) >= cap:
                    break
            next_token = resp.get("NextToken")
            if not next_token or len(collected) >= cap:
                break
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        msg = e.response.get("Error", {}).get("Message", str(e))
        return {"success": False, "error": f"{code}: {msg}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "events": collected,
        "count": len(collected),
        "region": region,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "lookup": {"AttributeKey": "ResourceName", "AttributeValue": resource_name},
    }


def parse_console_csv_or_excel(file_storage) -> dict[str, Any]:
    """
    Parse a CloudTrail / AWS console CSV export (UTF-8). Returns rows + columns.
    Does not execute AWS calls.
    """
    if not file_storage or not getattr(file_storage, "filename", None):
        return {"success": False, "error": "No file uploaded."}

    raw = file_storage.read()
    if not raw:
        return {"success": False, "error": "Empty file."}

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"success": False, "error": "File must be UTF-8 (save CSV as UTF-8 from console)."}

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = reader.fieldnames or []
    rows: list[dict[str, str]] = []
    for i, row in enumerate(reader):
        if i >= 500:
            break
        rows.append({k: (v if v is not None else "") for k, v in row.items()})

    return {
        "success": True,
        "columns": list(columns),
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= 500,
    }
