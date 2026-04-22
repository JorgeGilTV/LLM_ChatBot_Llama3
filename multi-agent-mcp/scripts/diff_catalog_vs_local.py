#!/usr/bin/env python3
"""Compare Datadog Software Catalog (service kind) to local APM wall fallback list."""
import os
import sys

# Project root: scripts/ -> parent
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

import re
import requests

from tools.status_monitor_service_lists import (
    ADT_MONITOR_SERVICES,
    GENERAL_MONITOR_SERVICES,
    SOFTWARE_CATALOG_TREEMAP_EXTRAS,
)


def local_union() -> set:
    s = set(ADT_MONITOR_SERVICES) | set(GENERAL_MONITOR_SERVICES) | set(
        SOFTWARE_CATALOG_TREEMAP_EXTRAS
    )
    return {x.strip() for x in s if x and str(x).strip()}


def fetch_catalog_names(
    max_entities: int = 500,
) -> set | None:
    dd = os.getenv("DATADOG_API_KEY")
    app = os.getenv("DATADOG_APP_KEY")
    site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if not dd or not app:
        return None
    base = f"https://{site}/api/v2/catalog/entity"
    headers = {
        "DD-API-KEY": dd,
        "DD-APPLICATION-KEY": app,
        "Accept": "application/json",
    }
    out: set = set()
    offset = 0
    limit = 100
    while len(out) < max_entities and offset < max_entities * 2:
        r = requests.get(
            base,
            headers=headers,
            params={
                "page[offset]": offset,
                "page[limit]": limit,
                "filter[kind]": "service",
                "includeDiscovered": "true",
            },
            timeout=60,
        )
        if r.status_code != 200:
            print(f"API {r.status_code}: {(r.text or '')[:400]}", file=sys.stderr)
            return None
        rows = (r.json() or {}).get("data") or []
        if not rows:
            break
        for item in rows:
            if not isinstance(item, dict):
                continue
            attr = item.get("attributes")
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            if not name and item.get("id"):
                iid = str(item.get("id") or "")
                for token in re.findall(r"(?:[a-z0-9][a-z0-9._-]+)", iid, re.I):
                    token = re.sub(r"^service[._]?", "", token, flags=re.I)
                    if 2 < len(token) < 200 and re.match(
                        r"^[a-z0-9][a-z0-9._-]*$", token, re.I
                    ):
                        name = token
                        break
            if name:
                out.add(name)
        if len(rows) < limit:
            break
        offset += limit
    return out


def main() -> None:
    local = local_union()
    print(f"Local static union (ADT+GENERAL+TREEMAP_EXTRAS): {len(local)}")
    remote = fetch_catalog_names()
    if remote is None:
        print(
            "No Datadog API list (set DATADOG_API_KEY + DATADOG_APP_KEY, or check API error)."
        )
        print(
            "\nRazón típica 127 vs ~118: la UI cuenta **todos** los servicios con tag "
            "env:production; OneView usa una **lista estática** alineada a monitores/cuadros, "
            "no sincroniza sola con el catálogo completo."
        )
        return
    print(f"Datadog catalog API (service kind, filtered by env tag when present): {len(remote)}")
    in_dd_not_local = sorted(remote - local, key=str.lower)
    in_local_not_dd = sorted(local - remote, key=str.lower)
    print(f"\nEn Datadog (API) y NO en lista local: {len(in_dd_not_local)}")
    for n in in_dd_not_local:
        print(f"  - {n}")
    print(f"\nEn lista local y NO en respuesta API (mismo lote): {len(in_local_not_dd)}")
    for n in in_local_not_dd:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
