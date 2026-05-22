"""
Engineering team buckets for APM Status Wall (matches org Status Wall layout).

Groups and service names are aligned to the Datadog / Arlo engineering Status Wall
(Xcloud, Partner, Platform, Smart Vision Streaming, etc.). Unlisted services fall under Other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAPPING_PATH = _REPO_ROOT / "lists" / "apm_engineering_groups.json"

ENGINEERING_GROUP_ORDER: tuple[str, ...] = (
    "Xcloud Engineering",
    "Partner Engineering",
    "Platform Engineering",
    "Smart Vision Streaming",
    "Core Services",
    "Sre",
    "Subscription Engineering",
    "Onecloud Engineering",
    "Verisure Engineering",
    "Cicd",
    "Windows",
    "Client Engineering",
    "Noc",
    "Npnoc",
    "Firmware",
    "Oci",
    "Smart Vision Computing",
    "Other",
)

# Canonical service name -> engineering group (lowercase keys)
_SERVICE_TO_GROUP: dict[str, str] = {
    # Xcloud Engineering
    "advisor": "Xcloud Engineering",
    "backend-log-server": "Xcloud Engineering",
    "broker-service": "Xcloud Engineering",
    "collector": "Xcloud Engineering",
    "device-authentication": "Xcloud Engineering",
    "device-location": "Xcloud Engineering",
    "directory": "Xcloud Engineering",
    "discovery": "Xcloud Engineering",
    "geolocation": "Xcloud Engineering",
    "history": "Xcloud Engineering",
    "logger": "Xcloud Engineering",
    "messaging": "Xcloud Engineering",
    "mqtt-auth": "Xcloud Engineering",
    "oauth": "Xcloud Engineering",
    "oauth-proxy": "Xcloud Engineering",
    "partner-proxy": "Xcloud Engineering",
    "policy": "Xcloud Engineering",
    "presence": "Xcloud Engineering",
    "registration": "Xcloud Engineering",
    "secret-manager": "Xcloud Engineering",
    "support": "Xcloud Engineering",
    # Partner Engineering
    "backend-arlosafeapi": "Partner Engineering",
    "arlosafeapi": "Partner Engineering",
    "backend-hmsalexaapi": "Partner Engineering",
    "backend-hmsentityauth": "Partner Engineering",
    "backend-hmsfwa": "Partner Engineering",
    "backend-hmshomekit-app": "Partner Engineering",
    "backend-arlosafepartners": "Partner Engineering",
    "backend-hmshomekit-scheduler": "Partner Engineering",
    "backend-hmsreportingservice": "Partner Engineering",
    "backend-partnercloud": "Partner Engineering",
    "backend-hmsapi-verisure": "Partner Engineering",
    "backend-hmsifttt": "Partner Engineering",
    "backend-partnerplatform": "Partner Engineering",
    "backend-hmshomekit-test": "Partner Engineering",
    "hmshomekit-test": "Partner Engineering",
    "backend-hmsgoogleapi": "Partner Engineering",
    # Platform Engineering
    "backend-arloautomation-leader": "Platform Engineering",
    "backend-hmsam": "Platform Engineering",
    "backend-hmsautomation": "Platform Engineering",
    "backend-hmsautomation-job": "Platform Engineering",
    "backend-hmsclientauth": "Platform Engineering",
    "backend-hmsclientsauth": "Platform Engineering",
    "backend-hmscsapi": "Platform Engineering",
    "backend-hmscscapi": "Platform Engineering",
    "backend-hmsautomation-scheduler": "Platform Engineering",
    "backend-hmsdeviceauth": "Platform Engineering",
    "backend-hmsdevicesauth": "Platform Engineering",
    "backend-hmsdeviceshadow": "Platform Engineering",
    "backend-hmscspubsub": "Platform Engineering",
    "backend-hmspubsub": "Platform Engineering",
    "hmsssocallback": "Platform Engineering",
    "backend-hmsdeviceversioncontrol": "Platform Engineering",
    "backend-hmsclientmanagement": "Platform Engineering",
    "backend-cloudplatform": "Platform Engineering",
    # Smart Vision Streaming
    "backend-hmsvideoverification": "Smart Vision Streaming",
    "backend-hmsvideooverification": "Smart Vision Streaming",
    "backend-feedsearch": "Smart Vision Streaming",
    "backend-mediamigrationscheduler": "Smart Vision Streaming",
    "mediamigrationscheduler": "Smart Vision Streaming",
    "backend-sipserver-app": "Smart Vision Streaming",
    "backend-hmsdeviceevents": "Smart Vision Streaming",
    "backend-hmslostreamingserver": "Smart Vision Streaming",
    "hmsarlostreamingserver": "Smart Vision Streaming",
    "backend-videoservice": "Smart Vision Streaming",
    "backend-videoservice-lb": "Smart Vision Streaming",
    "backend-videoservice-discovery": "Smart Vision Streaming",
    "backend-ajpserver-app": "Smart Vision Streaming",
    "backend-ajp": "Smart Vision Streaming",
    # Core Services
    "backend-hmsguard": "Core Services",
    "backend-arlosafelocations": "Core Services",
    "backend-hmsnotification": "Core Services",
    "hmsfeeds": "Core Services",
    "backend-hmssecurity": "Core Services",
    "hmssecurity": "Core Services",
    "backend-hmsalerts": "Core Services",
    "backend-hmsfeedg": "Core Services",
    "hmsfeedg": "Core Services",
    # Sre
    "nginx-clientapi": "Sre",
    "nginx-partnerapi-prod": "Sre",
    "nginx-clientapi-partner": "Sre",
    "nginx-deviceapi-partner": "Sre",
    "nginx-deviceapi": "Sre",
    "nginx-deviceapi-prod": "Sre",
    "nginx-partnerapi-z2-prod": "Sre",
    "nginx-partner": "Sre",
    "nginx-partnerapi-r2-prod": "Sre",
    "nginx-api-v2-prod": "Sre",
    # Subscription Engineering
    "backend-hmspayment": "Subscription Engineering",
    "backend-hmsdevicemanagement": "Subscription Engineering",
    "backend-inapppayments": "Subscription Engineering",
    "google-pubsub": "Subscription Engineering",
    "backend-supporttool": "Subscription Engineering",
    "hmscallbacks": "Subscription Engineering",
    "device-service": "Subscription Engineering",
    # Onecloud Engineering
    "camsdk-webserver": "Onecloud Engineering",
    "oc-notifications": "Onecloud Engineering",
    "ocapi": "Onecloud Engineering",
    "ocapi-z2": "Onecloud Engineering",
    "ocapi-r2": "Onecloud Engineering",
    # Verisure Engineering
    "backend-hmsapi": "Verisure Engineering",
    "backend-partner-notifications": "Verisure Engineering",
    "backend-partnernotifications": "Verisure Engineering",
    # Cicd
    "artifactory": "Cicd",
    # Windows
    "arlo": "Windows",
    "arlo-http-client": "Windows",
    "diagtool_mvc": "Windows",
    "diagtool_mvc-http-client": "Windows",
    "diagtool_nwc": "Windows",
    # Client Engineering
    "hmsweb": "Client Engineering",
    "backend-hmsweb-device": "Client Engineering",
    "backend-hmsweb-media": "Client Engineering",
    "backend-hmsweb-web": "Client Engineering",
    # Noc
    "cachet": "Noc",
    # Npnoc
    "aws.dynamodb": "Npnoc",
    # Firmware
    "asl-java": "Firmware",
    # Oci
    "vertex-ws": "Oci",
    "vertex-wa": "Oci",
    # Smart Vision Computing
    "savant-sagemaker": "Smart Vision Computing",
    # ADT / partner extras often on ADT wall
    "backend-hmsweb-device": "Client Engineering",
    "backend-notificationservice": "Platform Engineering",
    "backend-partner-api": "Partner Engineering",
    "backend-image": "Smart Vision Streaming",
    "cw-exp-prometheus": "Platform Engineering",
    "resources": "Other",
    "root-servlet": "Other",
}


def _norm_service_key(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip().lower())


def canonical_service_name(name: str) -> str:
    k = _norm_service_key(name)
    if not k:
        return ""
    return _SERVICE_TO_GROUP.get(k, k)


def engineering_group_for_service(name: str) -> str:
    k = _norm_service_key(name)
    if not k:
        return "Other"
    return _SERVICE_TO_GROUP.get(k, "Other")


def apm_engineering_groups_enabled() -> bool:
    import os

    v = (os.getenv("APM_STATUS_WALL_ENGINEERING_GROUPS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _overall_from_services(services: list[dict]) -> str:
    if any((s.get("status") or "") == "critical" for s in services):
        return "critical"
    if any((s.get("status") or "") == "warning" for s in services):
        return "warning"
    return "healthy"


def _counts_from_services(services: list[dict]) -> dict:
    h = sum(1 for s in services if s.get("status") == "healthy")
    w = sum(1 for s in services if s.get("status") == "warning")
    c = sum(1 for s in services if s.get("status") == "critical")
    return {
        "healthy": h,
        "warning": w,
        "critical": c,
        "unknown": 0,
        "inactive": 0,
        "total": len(services),
    }


def build_engineering_sections(serialized_services: list[dict]) -> list[dict]:
    """
    Split serialized wall tiles into engineering team sections (each with services list).
    Preserves per-service dicts from _wall_serialize_status.
    """
    buckets: dict[str, list] = {g: [] for g in ENGINEERING_GROUP_ORDER}
    for s in serialized_services or []:
        if not isinstance(s, dict):
            continue
        g = engineering_group_for_service(str(s.get("service") or ""))
        buckets.setdefault(g, []).append(s)

    out: list[dict] = []
    for label in ENGINEERING_GROUP_ORDER:
        svcs = buckets.get(label) or []
        if not svcs:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "other"
        out.append(
            {
                "key": slug,
                "label": label,
                "slug": slug,
                "overall": _overall_from_services(svcs),
                "counts": _counts_from_services(svcs),
                "services": svcs,
            }
        )
    return out


def all_mapped_service_names() -> set[str]:
    return {_norm_service_key(k) for k in _SERVICE_TO_GROUP}


def diff_services_vs_mapping(service_names: list[str]) -> dict:
    """Compare a list of APM service names to the engineering map (for reports)."""
    mapped = all_mapped_service_names()
    in_list = {_norm_service_key(n) for n in service_names if (n or "").strip()}
    image_canonical = {_norm_service_key(k) for k in _SERVICE_TO_GROUP}
    return {
        "in_dev_not_in_map": sorted(in_list - mapped),
        "in_map_not_in_dev": sorted(image_canonical - in_list),
        "in_dev": sorted(in_list),
        "in_map": sorted(mapped),
    }


def load_bundled_lists_union() -> list[str]:
    names: list[str] = []
    for fname in (
        "adt_apm_services.txt",
        "production_apm_127.txt",
        "samsung_apm_services.txt",
        "goldendev_apm_services.txt",
        "goldenqa_apm_services.txt",
        "qa_apm_services.txt",
    ):
        p = _REPO_ROOT / "lists" / fname
        if not p.is_file():
            continue
        with p.open(encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s and not s.startswith("#"):
                    names.append(s)
    return names


if __name__ == "__main__":
    d = diff_services_vs_mapping(load_bundled_lists_union())
    print(json.dumps(d, indent=2))
