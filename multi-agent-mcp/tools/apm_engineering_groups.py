"""
Engineering team buckets for APM Status Wall.

Production can mirror Datadog Software Catalog groupBy=Team (catalog entity `owner` slug).
Fallback: static map aligned to the org Status Wall screenshot.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAPPING_PATH = _REPO_ROOT / "lists" / "apm_engineering_groups.json"

# Visual order matches org Status Wall screenshot (5 columns × rows, left→right, top→bottom).
ENGINEERING_GROUP_ORDER: tuple[str, ...] = (
    "Xcloud Engineering",
    "Partner Engineering",
    "Platform Engineering",
    "Core Services",
    "Smart Vision Streaming",
    "Smart Vision",
    "Oci",
    "Samsung Partner",
    "Sre",
    "Subscription Engineering",
    "Onecloud Engineering",
    "Windows",
    "Cicd",
    "Firmware",
    "Client Engineering",
    "Noc",
    "Verisure Engineering",
    "Ecommerce",
    "Infrared Services",
    "Npnoc",
    "Other",
)

# Org Status Wall: four columns (stacks top→bottom, left→right).
ENGINEERING_COLUMN_SLUGS: tuple[tuple[str, ...], ...] = (
    (
        "xcloud-engineering",
        "platform-engineering",
    ),
    (
        "partner-engineering",
        "core-services",
        "samsung-partner",
        "sre",
    ),
    (
        "subscription-engineering",
        "verisure-engineering",
        "windows",
        "onecloud-engineering",
        "ecommerce",
        "infrared-services",
        "client-engineering",
        "firmware",
    ),
    ("cicd", "smart-vision-streaming", "smart-vision", "oci", "noc"),
)

# ADT org wall: Platform under Partner; Core Services under Other.
ENGINEERING_COLUMN_SLUGS_ADT: tuple[tuple[str, ...], ...] = (
    ("xcloud-engineering",),
    (
        "partner-engineering",
        "platform-engineering",
        "samsung-partner",
        "sre",
    ),
    (
        "subscription-engineering",
        "verisure-engineering",
        "windows",
        "onecloud-engineering",
        "ecommerce",
        "infrared-services",
        "client-engineering",
        "firmware",
    ),
    ("cicd", "smart-vision-streaming", "smart-vision", "oci", "noc", "core-services"),
)


def engineering_column_layout(dd_env: str = "") -> list[list[str]]:
    """Return column slug lists for the Status Wall frontend."""
    env = (dd_env or "").strip()
    if env in ("adt_prod", "cat_prod", "comcast_prod"):
        return [list(col) for col in ENGINEERING_COLUMN_SLUGS_ADT]
    return [list(col) for col in ENGINEERING_COLUMN_SLUGS]


# DD envs that use the org Status Wall engineering mosaic (team blocks + column layout).
ENGINEERING_WALL_DD_ENVS: tuple[str, ...] = (
    "adt_prod",
    "cat_prod",
    "comcast_prod",
    "production",
    "goldendev",
    "goldenqa",
)
GOLDEN_WALL_DD_ENVS: tuple[str, ...] = ("goldendev", "goldenqa")


def _is_golden_wall_env(dd_env: str) -> bool:
    return (dd_env or "").strip() in GOLDEN_WALL_DD_ENVS


def engineering_wall_uses_org_catalog(dd_env: str) -> bool:
    return (dd_env or "").strip() in ENGINEERING_WALL_DD_ENVS


# Datadog Software Catalog `attributes.owner` (same as /software?groupBy=Team).
OWNER_SLUG_TO_LABEL: dict[str, str] = {
    "xcloud-engineering": "Xcloud Engineering",
    "partner-engineering": "Partner Engineering",
    "platform-engineering": "Platform Engineering",
    "smart-vision-streaming": "Smart Vision Streaming",
    "smart-vision-computer-vision": "Smart Vision",
    "subscription-engineering": "Subscription Engineering",
    "onecloud-engineering": "Onecloud Engineering",
    "core-services": "Core Services",
    "sre": "Sre",
    "windows": "Windows",
    "verisure-engineering": "Verisure Engineering",
    "cicd": "Cicd",
    "client-engineering": "Client Engineering",
    "firmware": "Firmware",
    "noc": "Noc",
    "npnoc": "Npnoc",
    "samsung-partner": "Samsung Partner",
    "ecommerce": "Ecommerce",
    "infra-architecture": "Infra Architecture",
    "infrared-services": "Infrared Services",
    "partner-platform": "Partner Platform",
}

_catalog_owner_cache: dict = {}


def apm_status_wall_use_dd_team(dd_env: str) -> bool:
    """Group org-wall tiles by Datadog catalog owner (Software UI groupBy=Team)."""
    if (dd_env or "").strip() not in ENGINEERING_WALL_DD_ENVS:
        return False
    raw = (os.getenv("APM_STATUS_WALL_DD_TEAM") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return apm_engineering_groups_enabled()


def _label_from_owner_slug(owner_slug: str) -> str:
    slug = (owner_slug or "").strip().lower()
    if not slug:
        return "Other"
    if slug in OWNER_SLUG_TO_LABEL:
        return OWNER_SLUG_TO_LABEL[slug]
    return slug.replace("-", " ").title()


def fetch_datadog_catalog_service_owners(
    dd_api_key: str,
    dd_app_key: str,
    dd_site: str,
    *,
    max_entities: int = 800,
    cache_secs: int = 600,
) -> dict[str, str]:
    """
    service name (lower) -> catalog owner slug, from GET /api/v2/catalog/entity.
    """
    global _catalog_owner_cache
    now = time.time()
    site = (dd_site or "arlo.datadoghq.com").strip()
    hit = _catalog_owner_cache.get(site)
    if hit and (now - hit.get("at", 0)) < cache_secs:
        return dict(hit.get("owners") or {})

    import requests

    from tools.status_monitor import datadog_rest_api_base

    base = f"{datadog_rest_api_base(site)}/api/v2/catalog/entity"
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Accept": "application/json",
    }
    owners: dict[str, str] = {}
    offset = 0
    limit = 100
    cap = max(100, min(int(max_entities), 1200))
    while len(owners) < cap and offset < cap * 2:
        try:
            r = requests.get(
                base,
                headers=headers,
                params={
                    "page[offset]": offset,
                    "page[limit]": limit,
                    "filter[kind]": "service",
                    "includeDiscovered": "true",
                },
                timeout=(15, 60),
            )
        except Exception as e:
            print(f"⚠️ Catalog owner fetch failed: {e}")
            break
        if r.status_code != 200:
            print(f"⚠️ Catalog owner API {r.status_code}: {(r.text or '')[:200]}")
            break
        rows = (r.json() or {}).get("data") or []
        if not rows:
            break
        for item in rows:
            if not isinstance(item, dict):
                continue
            attr = item.get("attributes") or {}
            name = (attr.get("name") or "").strip()
            owner = (attr.get("owner") or "").strip().lower()
            if name:
                owners[_norm_service_key(name)] = owner
        if len(rows) < limit:
            break
        offset += limit
    _catalog_owner_cache[site] = {"at": now, "owners": owners}
    return owners


def _engineering_team_display_order(labels_seen: set[str]) -> list[str]:
    """Org column order first, then extra Datadog teams, then Other."""
    out: list[str] = []
    seen: set[str] = set()
    for label in ENGINEERING_GROUP_ORDER:
        if label in labels_seen and label not in seen:
            out.append(label)
            seen.add(label)
    extras = sorted(
        (x for x in labels_seen if x not in seen and x != "Other"),
        key=str.lower,
    )
    out.extend(extras)
    if "Other" in labels_seen and "Other" not in seen:
        out.append("Other")
    return out


# Org wall label -> alternate `service` names seen in Datadog (esp. production export).
_CATALOG_DD_ALIASES: dict[str, tuple[str, ...]] = {
    "backend-hmsgoogleapis": ("backend-hmsgoogleapi",),
    "backend-hmsreporting-service": ("backend-hmsreportingservice",),
    "backend-hmsvideoverification": ("backend-hmsvideooverification",),
    "backend-mediamigrationscheduler": ("mediamigrationscheduler",),
    "backend-mediamigration-scheduler": (
        "mediamigrationscheduler",
        "backend-mediamigrationscheduler",
    ),
    "hmsarlostreamingserver": ("backend-hmslostreamingserver",),
    "hmssecurity": ("backend-hmssecurity",),
    "backend-hmsnotifications": ("backend-hmsnotification",),
    "backend-hmsclientauth": ("backend-hmsclientsauth",),
    "backend-hmsdeviceauth": ("backend-hmsdevicesauth",),
    "backend-hmscsapi": ("backend-hmscscapi",),
    "arlosafeapi": ("backend-arlosafeapi",),
    "backend-arlosafe-partners": ("backend-arlosafepartners",),
}


def _row_with_display_service(row: dict, display_name: str) -> dict:
    out = dict(row)
    out["service"] = display_name
    return out


def _dd_keys_for_catalog_name(catalog_name: str) -> set[str]:
    k = _norm_service_key(catalog_name)
    keys = {k} if k else set()
    for alt in _CATALOG_DD_ALIASES.get(k, ()):
        ak = _norm_service_key(alt)
        if ak:
            keys.add(ak)
    return keys


def _lookup_status_for_catalog_name(catalog_name: str, by_key: dict[str, dict]) -> dict | None:
    k = _norm_service_key(catalog_name)
    if k in by_key:
        return _row_with_display_service(by_key[k], catalog_name)
    for alt in _CATALOG_DD_ALIASES.get(k, ()):
        ak = _norm_service_key(alt)
        if ak in by_key:
            return _row_with_display_service(by_key[ak], catalog_name)
    return None


def merge_bundled_names_with_org_catalog(file_names: list[str]) -> list[str]:
    """Org wall names first (tile order), then extras from bundled file (no org-only names)."""
    if not apm_engineering_groups_enabled():
        seen: set[str] = set()
        out: list[str] = []
        for n in file_names or []:
            k = _norm_service_key(n)
            if k and k not in seen:
                seen.add(k)
                out.append((n or "").strip())
        return out
    scope_keys = {_norm_service_key(n) for n in (file_names or []) if (n or "").strip()}
    if not scope_keys:
        return list(file_names or [])
    org = engineering_wall_catalog_names()
    seen: set[str] = set()
    merged: list[str] = []
    for n in org:
        k = _norm_service_key(n)
        if k and k in scope_keys and k not in seen:
            seen.add(k)
            merged.append(n)
    for n in file_names or []:
        k = _norm_service_key(n)
        if k and k not in seen:
            seen.add(k)
            merged.append((n or "").strip())
    return merged


def order_services_for_engineering_wall(
    scope_names: list[str],
    *,
    dd_env: str = "",
    owner_by_service: dict[str, str] | None = None,
) -> list[str]:
    """Tile order: by Datadog Team (catalog owner) when enabled, else org wall order."""
    scope_keys = {_norm_service_key(n) for n in (scope_names or []) if (n or "").strip()}
    if not scope_keys:
        return []
    by_key: dict[str, str] = {}
    for n in scope_names or []:
        k = _norm_service_key(n)
        if k and k not in by_key:
            by_key[k] = (n or "").strip()

    if apm_status_wall_use_dd_team(dd_env) and owner_by_service:
        team_buckets: dict[str, list[str]] = {}
        for k, display in by_key.items():
            owner = (owner_by_service.get(k) or "").strip().lower()
            label = _label_from_owner_slug(owner)
            team_buckets.setdefault(label, []).append(display)
        labels_seen = set(team_buckets.keys())
        out: list[str] = []
        for label in _engineering_team_display_order(labels_seen):
            names = team_buckets.get(label) or []
            wall_order = ENGINEERING_WALL_SERVICE_ORDER.get(label, ())
            if wall_order:
                rank = {_norm_service_key(n): i for i, n in enumerate(wall_order)}
                names.sort(
                    key=lambda n: (
                        rank.get(_norm_service_key(n), 9999),
                        str(n).lower(),
                    )
                )
            else:
                names.sort(key=str.lower)
            out.extend(names)
        return out

    out: list[str] = []
    placed: set[str] = set()
    for label in ENGINEERING_GROUP_ORDER:
        if label == "Other":
            continue
        for name in ENGINEERING_WALL_SERVICE_ORDER.get(label, ()):
            k = _norm_service_key(name)
            if k in scope_keys and k not in placed:
                out.append(by_key.get(k, name))
                placed.add(k)
    extras = sorted(
        (by_key[k] for k in by_key if k not in placed),
        key=str.lower,
    )
    out.extend(extras)
    return out


def _engineering_wall_active_only() -> bool:
    import os

    raw = (os.getenv("SOFTWARE_CATALOG_WALL_ACTIVE_ONLY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# Tile order within each team block (org Status Wall screenshot, left→right top→bottom).
# Warn/crit tiles keep larger spans via frontend appendTilesToGrid sizing rules.
ENGINEERING_WALL_SERVICE_ORDER: dict[str, tuple[str, ...]] = {
    "Xcloud Engineering": (
        "advisor",
        "backend-log-server",
        "collector",
        "oauth",
        "broker-service",
        "device-authentication",
        "device-location",
        "directory",
        "discovery",
        "geolocation",
        "history",
        "mqtt-auth",
        "oauth-proxy",
        "partner-proxy",
        "logger",
        "policy",
        "registration",
        "secret-manager",
        "messaging",
        "presence",
        "support",
    ),
    "Partner Engineering": (
        "backend-hmsalexaapi",
        "hmsalexaapi",
        "backend-hmsapi-verisure",
        "backend-hmsentityauth",
        "backend-hmsfwa",
        "backend-hmsgoogleapis",
        "backend-hmsifttt",
        "backend-hmsreporting-service",
        "backend-hmsreportingservice",
        "backend-hmshomekit-app",
        "backend-partnercloud",
        "hmshomekit-test",
        "backend-hmshomekit-test",
        "backend-arlosafepartners",
        "backend-hmshomekit-scheduler",
        "backend-partnerplatform",
    ),
    "Platform Engineering": (
        "backend-hmscspubsub",
        "backend-hmspubsub",
        "backend-arloautomation-leader",
        "backend-hmsautomation-job",
        "backend-hmsautomation-scheduler",
        "backend-hmsam",
        "backend-hmsclientauth",
        "backend-hmsdeviceauth",
        "backend-hmsautomation",
        "backend-hmscsapi",
        "backend-hmsdeviceshadow",
        "hmsssocallback",
    ),
    "Smart Vision Streaming": (
        "backend-feedsearch",
        "backend-hmsvideoverification",
        "backend-hmsvideooverification",
        "backend-mediamigrationscheduler",
        "backend-mediamigration-scheduler",
        "backend-hmsdeviceevents",
        "backend-sipserver-app",
        "hmsarlostreamingserver",
        "mediamigrationscheduler",
    ),
    "Core Services": (
        "backend-hmsguard",
        "backend-arlosafelocations",
        "backend-hmsnotification",
        "backend-hmsnotifications",
        "backend-arlosafeapi",
        "arlosafeapi",
        "hmsfeeds",
        "hmssecurity",
        "backend-hmssecurity",
        "hmsfeedg",
    ),
    "Sre": (
        "nginx-clientapi",
        "nginx-deviceapi-partner",
        "nginx-clientapi-partner",
        "nginx-deviceapi",
        "nginx-partnerapi-prod",
        "nginx-partnerapi-z2-prod",
    ),
    "Subscription Engineering": (
        "backend-hmspayment",
        "backend-hmsdevicemanagement",
        "backend-inapppayments",
        "google-pubsub",
        "backend-supporttool",
        "hmscallbacks",
    ),
    "Windows": (
        "arlo",
        "arlo-http-client",
        "diagtool_mvc",
        "diagtool_mvc-http-client",
    ),
    "Verisure Engineering": (
        "backend-hmsapi",
        "hmsapi-verisure",
        "backend-partner-notifications",
    ),
    "Onecloud Engineering": (
        "camsdk-webserver",
        "oc-notifications",
        "ocapi",
        "ocapi-z2",
    ),
    "Cicd": ("artifactory",),
    "Client Engineering": ("hmsweb",),
    "Firmware": ("asl-java",),
    "Noc": ("cachet",),
    "Npnoc": ("aws.dynamodb",),
    "Smart Vision": ("savant-sagemaker",),
    "Oci": ("vertex-ws",),
}

# Fixed column count per team block (matches org wall tile density).
ENGINEERING_WALL_TILE_COLUMNS: dict[str, int] = {
    "Xcloud Engineering": 3,
    "Partner Engineering": 4,
    "Platform Engineering": 4,
    "Smart Vision Streaming": 3,
    "Samsung Partner": 3,
    "Core Services": 3,
    "Sre": 2,
    "Subscription Engineering": 3,
    "Windows": 2,
    "Ecommerce": 2,
    "Infrared Services": 2,
    "Verisure Engineering": 2,
    "Onecloud Engineering": 2,
    "Cicd": 2,
    "Client Engineering": 2,
    "Firmware": 2,
    "Noc": 2,
    "Npnoc": 2,
    "Smart Vision": 2,
    "Oci": 2,
    "Other": 3,
}

_SLUG_BY_LABEL = {
    re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "other": label
    for label in ENGINEERING_GROUP_ORDER
}


def engineering_wall_catalog_names() -> list[str]:
    """All org-wall services in display order (deduped, first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for label in ENGINEERING_GROUP_ORDER:
        if label == "Other":
            continue
        for name in ENGINEERING_WALL_SERVICE_ORDER.get(label, ()):
            k = _norm_service_key(name)
            if k and k not in seen:
                seen.add(k)
                out.append(name)
    return out


def engineering_wall_tile_columns_for_slug(slug: str) -> int:
    label = _SLUG_BY_LABEL.get(slug or "", "")
    return int(ENGINEERING_WALL_TILE_COLUMNS.get(label, 2))


def sort_services_for_engineering_group(label: str, services: list[dict]) -> list[dict]:
    """Order tiles to match org Status Wall; unknown extras appended alphabetically."""
    order = ENGINEERING_WALL_SERVICE_ORDER.get(label, ())
    if not order:
        return sorted(services or [], key=lambda s: str(s.get("service") or "").lower())
    rank = {_norm_service_key(n): i for i, n in enumerate(order)}
    by_key: dict[str, dict] = {}
    for s in services or []:
        if not isinstance(s, dict):
            continue
        k = _norm_service_key(str(s.get("service") or ""))
        if k and k not in by_key:
            by_key[k] = s

    out: list[dict] = []
    placed: set[str] = set()
    for name in order:
        k = _norm_service_key(name)
        if k in by_key and k not in placed:
            out.append(by_key[k])
            placed.add(k)
    extras = [by_key[k] for k in by_key if k not in placed]
    extras.sort(key=lambda s: str(s.get("service") or "").lower())
    out.extend(extras)
    return out


def merge_engineering_wall_statuses(
    all_statuses: list[dict],
    dd_env: str,
    environment: str,
    *,
    scope_service_names: list[str] | None = None,
) -> list[dict]:
    """
    For org-wall envs (production, adt_prod, goldendev, goldenqa): layout order from
    scope_service_names (Datadog APM / bundled list) or the static org catalog.
    Only tiles with APM traffic by default (golden: active only, no idle legacy fill).
    Resolves DD alias names (e.g. backend-hmsgoogleapi -> backend-hmsgoogleapis).
    """
    active = ("healthy", "warning", "critical")
    if not apm_engineering_groups_enabled() or not engineering_wall_uses_org_catalog(
        dd_env
    ):
        return [s for s in (all_statuses or []) if (s.get("status") or "") in active]

    if scope_service_names:
        catalog = order_services_for_engineering_wall(scope_service_names)
    else:
        catalog = engineering_wall_catalog_names()

    by_key: dict[str, dict] = {}
    for s in all_statuses or []:
        if not isinstance(s, dict):
            continue
        k = _norm_service_key(str(s.get("service") or ""))
        if k:
            by_key[k] = s

    active_only = (
        _engineering_wall_active_only()
        and engineering_wall_uses_org_catalog(dd_env)
    )
    out: list[dict] = []
    consumed_dd: set[str] = set()
    for name in catalog:
        row = _lookup_status_for_catalog_name(name, by_key)
        legacy_org = (
            active_only
            and engineering_wall_uses_org_catalog(dd_env)
            and not _is_golden_wall_env(dd_env)
            and is_org_wall_legacy_service(name, dd_env)
        )
        if row is not None:
            st = row.get("status") or "inactive"
            if active_only and st not in active:
                if legacy_org:
                    out.append(row)
                consumed_dd.update(_dd_keys_for_catalog_name(name))
                continue
            if not active_only and st in (*active, "inactive", "unknown"):
                out.append(row)
            elif active_only and st in active:
                out.append(row)
        elif legacy_org:
            out.append(
                {
                    "service": name,
                    "status": "healthy",
                    "environment": environment,
                    "wall_idle": True,
                }
            )
        consumed_dd.update(_dd_keys_for_catalog_name(name))

    for s in all_statuses or []:
        k = _norm_service_key(str(s.get("service") or ""))
        if not k or k in consumed_dd:
            continue
        if (s.get("status") or "") in active:
            out.append(s)
            consumed_dd.add(k)
    return out


_ORG_WALL_LEGACY_LIST_BY_ENV: dict[str, Path] = {
    "production": _REPO_ROOT / "lists" / "production_apm_127.txt",
    "adt_prod": _REPO_ROOT / "lists" / "adt_apm_services.txt",
    "cat_prod": _REPO_ROOT / "lists" / "cat_apm_services.txt",
    "comcast_prod": _REPO_ROOT / "lists" / "comcast_apm_services.txt",
}


def org_wall_legacy_list_path(dd_env: str) -> Path | None:
    return _ORG_WALL_LEGACY_LIST_BY_ENV.get((dd_env or "").strip())


def org_wall_legacy_service_names(dd_env: str = "production") -> list[str]:
    """Bundled org wall list for production or adt_prod."""
    path = org_wall_legacy_list_path(dd_env)
    if path is None or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        s = ln.strip()
        if not s or s.lstrip().startswith("#"):
            continue
        k = _norm_service_key(s)
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def org_wall_legacy_service_keys(dd_env: str = "production") -> set[str]:
    keys: set[str] = set()
    for name in org_wall_legacy_service_names(dd_env):
        keys.update(_dd_keys_for_catalog_name(name))
        keys.add(_norm_service_key(name))
    return keys


def is_org_wall_legacy_service(name: str, dd_env: str = "production") -> bool:
    """True if name is in the env org wall bundled list (incl. DD aliases)."""
    legacy = org_wall_legacy_service_keys(dd_env)
    if not legacy:
        return False
    k = _norm_service_key(name)
    if not k:
        return False
    return k in legacy or bool(_dd_keys_for_catalog_name(name) & legacy)


def normalize_org_wall_legacy_tile_statuses(
    statuses: list[dict],
    dd_env: str,
) -> list[dict]:
    """
    Org wall bundled list (production / adt_prod): show idle/unknown tiles as healthy
    (green OK) unless Datadog reported warning or critical.
    """
    if not engineering_wall_uses_org_catalog(dd_env):
        return list(statuses or [])
    out: list[dict] = []
    for s in statuses or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("service") or "")
        if not is_org_wall_legacy_service(name, dd_env):
            out.append(s)
            continue
        st = (s.get("status") or "inactive").strip().lower()
        if st in ("warning", "critical"):
            out.append(s)
            continue
        if st in ("inactive", "unknown"):
            row = dict(s)
            row["status"] = "healthy"
            row["wall_idle"] = True
            out.append(row)
            continue
        out.append(s)
    return out


def merge_apm_names_with_org_wall_legacy(
    apm_names: list[str],
    dd_env: str = "production",
) -> tuple[list[str], int]:
    """
    Union the env org wall bundled list into the Datadog APM scope so legacy names
    stay in scope even when missing from GET /api/v2/apm/services.
    """
    if not engineering_wall_uses_org_catalog(dd_env):
        return list(apm_names or []), 0
    legacy = org_wall_legacy_service_names(dd_env)
    if not legacy:
        return list(apm_names or []), 0
    by_key: dict[str, str] = {}
    for n in apm_names or []:
        k = _norm_service_key(n)
        if k and k not in by_key:
            by_key[k] = (n or "").strip()
    added = 0
    for n in legacy:
        k = _norm_service_key(n)
        if k and k not in by_key:
            by_key[k] = n
            added += 1
    out: list[str] = []
    seen: set[str] = set()
    for n in apm_names or []:
        k = _norm_service_key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(by_key[k])
    for n in legacy:
        k = _norm_service_key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(by_key[k])
    return out, added


def drop_other_unlisted_org_wall_tiles(
    statuses: list[dict],
    dd_env: str,
    owner_by_service: dict[str, str] | None,
) -> tuple[list[dict], int]:
    """
    Production/adt_prod + Datadog Team grouping: omit tiles that fall in Other and are
    not in the bundled org wall list for that env.
    """
    if not engineering_wall_uses_org_catalog(dd_env) or not apm_status_wall_use_dd_team(dd_env):
        return list(statuses or []), 0
    org_keys = org_wall_legacy_service_keys(dd_env)
    if not org_keys:
        return list(statuses or []), 0
    use_dd = owner_by_service is not None
    out: list[dict] = []
    dropped = 0
    for s in statuses or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("service") or "")
        k = _norm_service_key(name)
        if k in org_keys:
            out.append(s)
            continue
        group = engineering_group_for_service(
            name,
            dd_env=dd_env,
            owner_by_service=owner_by_service if use_dd else None,
        )
        if group == "Other":
            dropped += 1
            continue
        out.append(s)
    return out, dropped


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
    "backend-hmsalexaapi": "Partner Engineering",
    "hmsalexaapi": "Partner Engineering",
    "backend-hmsentityauth": "Partner Engineering",
    "backend-hmsfwa": "Partner Engineering",
    "backend-hmshomekit-app": "Partner Engineering",
    "backend-arlosafepartners": "Partner Engineering",
    "backend-arlosafe-partners": "Partner Engineering",
    "backend-hmshomekit-scheduler": "Partner Engineering",
    "backend-hmsreportingservice": "Partner Engineering",
    "backend-partnercloud": "Partner Engineering",
    "backend-hmsapi-verisure": "Partner Engineering",  # Partner wall tile; Verisure also lists hmsapi-verisure
    "backend-hmsifttt": "Partner Engineering",
    "backend-partnerplatform": "Partner Engineering",
    "backend-hmshomekit-test": "Partner Engineering",
    "hmshomekit-test": "Partner Engineering",
    "backend-hmsgoogleapi": "Partner Engineering",
    "backend-hmsgoogleapis": "Partner Engineering",
    "backend-hmsreporting-service": "Partner Engineering",
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
    "backend-mediamigration-scheduler": "Smart Vision Streaming",
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
    "backend-arlosafeapi": "Core Services",
    "arlosafeapi": "Core Services",
    "backend-arlosafelocations": "Core Services",
    "backend-hmsnotification": "Core Services",
    "backend-hmsnotifications": "Core Services",
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
    "hmsapi-verisure": "Verisure Engineering",
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
    # Smart Vision (org wall label; was Smart Vision Computing)
    "savant-sagemaker": "Smart Vision",
    # ADT / partner extras often on ADT wall
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


def engineering_group_for_service(
    name: str,
    *,
    dd_env: str = "",
    owner_by_service: dict[str, str] | None = None,
) -> str:
    k = _norm_service_key(name)
    if not k:
        return "Other"
    if apm_status_wall_use_dd_team(dd_env) and owner_by_service is not None:
        if k in owner_by_service:
            owner = (owner_by_service.get(k) or "").strip().lower()
            if owner:
                return _label_from_owner_slug(owner)
        return _SERVICE_TO_GROUP.get(k, "Other")
    return _SERVICE_TO_GROUP.get(k, "Other")


def apm_engineering_groups_enabled() -> bool:
    import os

    v = (os.getenv("APM_STATUS_WALL_ENGINEERING_GROUPS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def show_empty_engineering_groups() -> bool:
    """Show team blocks with zero services. Default off — org wall hides empty teams."""
    import os

    v = (os.getenv("APM_STATUS_WALL_SHOW_EMPTY_ENGINEERING_GROUPS") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


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
    unk = sum(1 for s in services if s.get("status") == "unknown")
    inn = sum(1 for s in services if s.get("status") == "inactive")
    return {
        "healthy": h,
        "warning": w,
        "critical": c,
        "unknown": unk,
        "inactive": inn,
        "total": len(services),
    }


def build_engineering_sections(
    serialized_services: list[dict],
    *,
    dd_env: str = "",
    owner_by_service: dict[str, str] | None = None,
) -> list[dict]:
    """
    Split serialized wall tiles into engineering team sections (each with services list).
    When APM_STATUS_WALL_DD_TEAM=1 on production, teams match Datadog Software Catalog owner.
    """
    use_dd_team = apm_status_wall_use_dd_team(dd_env) and owner_by_service is not None
    buckets: dict[str, list] = {}
    for s in serialized_services or []:
        if not isinstance(s, dict):
            continue
        g = engineering_group_for_service(
            str(s.get("service") or ""),
            dd_env=dd_env,
            owner_by_service=owner_by_service if use_dd_team else None,
        )
        buckets.setdefault(g, []).append(s)

    if use_dd_team:
        section_order = _engineering_team_display_order(set(buckets.keys()))
    else:
        section_order = list(ENGINEERING_GROUP_ORDER)

    show_empty = show_empty_engineering_groups()
    out: list[dict] = []
    for label in section_order:
        if label == "Other":
            if (
                apm_status_wall_use_dd_team(dd_env)
                and engineering_wall_uses_org_catalog(dd_env)
                and not _is_golden_wall_env(dd_env)
            ):
                continue
            if not (buckets.get("Other") or []):
                continue
        svcs = buckets.get(label) or []
        if not svcs and not show_empty:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "other"
        ordered = sort_services_for_engineering_group(label, svcs)
        order_names = list(ENGINEERING_WALL_SERVICE_ORDER.get(label, ()))
        entry = {
            "key": slug,
            "label": label,
            "slug": slug,
            "overall": _overall_from_services(ordered),
            "counts": _counts_from_services(ordered),
            "services": ordered,
            "service_order": order_names,
            "tile_columns": ENGINEERING_WALL_TILE_COLUMNS.get(label, 2),
        }
        if use_dd_team:
            entry["team_source"] = "datadog_catalog_owner"
        out.append(entry)
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
        "cat_apm_services.txt",
        "comcast_apm_services.txt",
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
