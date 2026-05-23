#!/usr/bin/env python3
"""Refresh lists/production_apm_127.txt from Datadog APM services (env=production)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

from tools.apm_engineering_groups import order_services_for_engineering_wall
from tools.status_monitor import _fetch_datadog_apm_service_names_for_env


def main() -> None:
    api = os.getenv("DATADOG_API_KEY")
    app = os.getenv("DATADOG_APP_KEY")
    site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if not api or not app:
        print("Set DATADOG_API_KEY and DATADOG_APP_KEY", file=sys.stderr)
        sys.exit(1)
    names = _fetch_datadog_apm_service_names_for_env(api, app, site, "production")
    if not names:
        print("No services returned from Datadog APM API", file=sys.stderr)
        sys.exit(1)
    ordered = order_services_for_engineering_wall(names)
    out_path = os.path.join(_ROOT, "lists", "production_apm_127.txt")
    lines = [
        "# APM service names for env:production — synced from Datadog GET /api/v2/apm/services",
        f"# Count: {len(ordered)} (Software Catalog UI ~129–133 for production)",
        "",
    ]
    lines.extend(ordered)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {len(ordered)} services to {out_path}")


if __name__ == "__main__":
    main()
