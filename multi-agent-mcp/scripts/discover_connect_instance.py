#!/usr/bin/env python3
"""List Amazon Connect instances and optionally write AWS_CONNECT_* into .env."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.aws_connect_monitor import _connect_probe_regions, _connect_client  # noqa: E402


def _discover() -> list[tuple[str, str, str]]:
    import os

    primary = (os.getenv("AWS_CONNECT_REGION") or os.getenv("AWS_REGION") or "us-east-1").strip()
    found: list[tuple[str, str, str]] = []
    for reg in _connect_probe_regions(primary):
        try:
            resp = _connect_client(reg).list_instances(MaxResults=25)
            for s in resp.get("InstanceSummaryList") or []:
                iid = str(s.get("Id") or "").strip()
                alias = str(s.get("InstanceAlias") or iid)
                if iid:
                    found.append((reg, iid, alias))
        except Exception as e:
            print(f"  {reg}: skip ({e})", file=sys.stderr)
    return found


def _write_dotenv(region: str, instance_id: str) -> None:
    dotenv = _REPO / ".env"
    text = dotenv.read_text(encoding="utf-8") if dotenv.exists() else ""
    updates = {
        "AWS_CONNECT_REGION": region,
        "AWS_CONNECT_INSTANCE_ID": instance_id,
    }
    for key, val in updates.items():
        pat = re.compile(rf"^#?\s*{re.escape(key)}=.*$", re.MULTILINE)
        line = f"{key}={val}"
        if pat.search(text):
            text = pat.sub(line, text, count=1)
        else:
            anchor = "AWS_REGION="
            idx = text.find(anchor)
            if idx >= 0:
                end = text.find("\n", idx)
                insert_at = len(text) if end < 0 else end + 1
                block = f"\n{line}\n"
                text = text[:insert_at] + block + text[insert_at:]
            else:
                text = text.rstrip() + f"\n{line}\n"
    dotenv.write_text(text, encoding="utf-8")
    print(f"Updated {dotenv} with AWS_CONNECT_REGION and AWS_CONNECT_INSTANCE_ID.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover Amazon Connect instances")
    ap.add_argument(
        "--write-dotenv",
        action="store_true",
        help="If exactly one instance is found, write AWS_CONNECT_* to .env",
    )
    args = ap.parse_args()
    rows = _discover()
    if not rows:
        print("No Connect instances found (check AWS creds and regions).", file=sys.stderr)
        return 1
    for reg, iid, alias in rows:
        print(f"{reg}\t{iid}\t{alias}")
    if args.write_dotenv:
        if len(rows) != 1:
            print(
                f"Refusing --write-dotenv: found {len(rows)} instances (need exactly 1).",
                file=sys.stderr,
            )
            return 2
        reg, iid, _ = rows[0]
        _write_dotenv(reg, iid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
