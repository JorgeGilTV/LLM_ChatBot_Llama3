#!/usr/bin/env python3
"""Validate lists/production_apm_127.txt matches the 90-tile production catalog."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.status_monitor_service_lists import (  # noqa: E402
    GENERAL_MONITOR_SERVICES,
    PRODUCTION_MONITOR_SERVICE_COUNT,
)


def _read_bundled(path: str) -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if s and not s.lstrip().startswith("#"):
                out.append(s)
    return out


def main() -> int:
    path = os.path.join(_ROOT, "lists", "production_apm_127.txt")
    bundled = _read_bundled(path)
    general = list(GENERAL_MONITOR_SERVICES)
    bset = {x.lower() for x in bundled}
    gset = {x.lower() for x in general}
    ok = len(bundled) == PRODUCTION_MONITOR_SERVICE_COUNT and bset == gset
    print(f"File: {path}")
    print(f"Bundled count: {len(bundled)} (expected {PRODUCTION_MONITOR_SERVICE_COUNT})")
    print(f"GENERAL_MONITOR_SERVICES: {len(general)}")
    if bset != gset:
        only_file = sorted(bset - gset)
        only_general = sorted(gset - bset)
        if only_file:
            print(f"In file only: {only_file}")
        if only_general:
            print(f"In GENERAL only: {only_general}")
    if ok:
        print("OK — production list valid (90 services).")
        return 0
    print("ERROR — production list mismatch.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
