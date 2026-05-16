#!/usr/bin/env python3
"""
Smoke-test outbound connections for integrations used by multi-agent-mcp.
Loads repo .env; does not print secret values (only OK/FAIL/SKIP + HTTP status or error class).

  python3 scripts/verify_tool_connections.py
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(_REPO, ".env"))
    except ImportError:
        pass


def _row(name: str, ok: bool | None, detail: str) -> None:
    """ok True=OK, False=FAIL, None=SKIP"""
    tag = "OK   " if ok is True else ("SKIP " if ok is None else "FAIL ")
    print(f"{tag} {name:22} {detail}")


_OPTIONAL_INCOMPLETE = frozenset({"grafana", "aws_sts"})


def _cli_row_ok_flag(it: dict) -> bool | None:
    """True=OK, False=FAIL, None=SKIP (opcional sin credenciales)."""
    oid = it.get("id")
    if it.get("row_ok"):
        if (
            oid in _OPTIONAL_INCOMPLETE
            and it.get("key_ok") is False
            and it.get("connection_ok") is None
        ):
            return None
        return True
    return False


def main() -> int:
    _load_env()
    from tools.test_connections import run_connection_checks

    data = run_connection_checks()
    fails = 0
    for it in data.get("items") or []:
        name = str(it.get("name") or it.get("id") or "?")
        flag = _cli_row_ok_flag(it)
        parts = [p for p in (it.get("detail"), it.get("error")) if p]
        detail = " | ".join(parts) if parts else ("OK" if flag is True else "")
        if flag is False:
            fails += 1
        _row(name, flag, detail)

    print()
    checked = data.get("checked_at", "")
    if fails:
        print(
            f"Summary: {fails} failure(s). Fix network/credentials or unset unused integrations. ({checked})"
        )
        return 1
    print(f"Summary: no failed checks (SKIP = optional or unset). ({checked})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
