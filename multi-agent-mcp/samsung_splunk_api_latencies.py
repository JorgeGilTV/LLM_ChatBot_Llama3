"""
Samsung "alarm latencies" charts from Splunk REST (port 8089) + token — same model as
multi-agent-mcp/tools/splunk_tool.py. No Splunk web UI / login in the browser.

Panel config per environment (prod, qa, dev), first match wins:
  - SPLUNK_SAMSUNG_STUDIO_JSON or spl/samsung_studio_dashboard.json  (Studio definition export: sections + splunk.line + dataSources)
  - If no file: same JSON can be read from Splunk via REST (GET /servicesNS/.../data/ui/views/<view>).
    App and view come from SPLUNK_DASHBOARD_PATH. Disable with SPLUNK_FETCH_STUDIO_FROM_REST=0
  - SPLUNK_SAMSUNG_SPL_<ENV>  (SPL; multiline in .env ok)
  - file: spl/samsung_<env>.spl
  - SPLUNK_SAMSUNG_SAVED_<ENV> = exact Saved Search name in app SPLUNK_APP → | savedsearch "…"
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
from datetime import datetime, timezone
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from pathlib import Path
from typing import Any

import requests
from zoneinfo import ZoneInfo

# Ensure bundled_mcp (multi-agent-mcp style tools) is importable when this file is the entry.
_BASE = Path(__file__).resolve().parent
_BUNDLED = _BASE / "bundled_mcp"
if str(_BUNDLED) not in sys.path:
    sys.path.insert(0, str(_BUNDLED))

from tools.splunk_tool import (  # noqa: E402
    splunk_display_timezone,
    splunk_p0_job_timezone,
    splunk_search_timezone,
    _splunk_row_epoch_seconds,
)

_SPL_DIR = _BASE / "spl"

# Match Splunk Dashboard Studio default line series (P50, P95, P99, Avg)
SPLUNK_STUDIO_DEFAULT_COLORS = ["#53a051", "#f8be34", "#dc4e41", "#0877a6"]


def _splunk_export_http_timeout() -> tuple[int, int]:
    """
    Connect and read timeouts for POST .../search/jobs/export.
    Wider time ranges (e.g. last hour across several panels) can exceed a few minutes; Gunicorn
    timeout (see GUNICORN_TIMEOUT) should be higher than the worst single export in parallel batch.
    """
    try:
        read = int((os.environ.get("SPLUNK_EXPORT_READ_TIMEOUT", "") or "600").strip() or "600")
    except ValueError:
        read = 600
    read = max(60, min(read, 7200))
    try:
        connect = int((os.environ.get("SPLUNK_EXPORT_CONNECT_TIMEOUT", "") or "30").strip() or "30")
    except ValueError:
        connect = 30
    connect = max(5, min(connect, 120))
    return (connect, read)


def _splunk_rest_get_timeout() -> int:
    """Timeout (seconds) for short Splunk REST GETs (e.g. fetch Studio view)."""
    try:
        t = int((os.environ.get("SPLUNK_REST_GET_TIMEOUT", "") or "90").strip() or "90")
    except ValueError:
        t = 90
    return max(15, min(t, 300))


# earliest_time / latest_time (export) — safe subset; validated on query string (?earliest= & ?latest=)


def sanitize_splunk_earliest(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if len(s) > 48 or ".." in s or "--" in s or "http" in s.lower():
        return None
    if re.fullmatch(r"[-+@0-9a-zA-Z._]+", s):
        return s
    return None


def sanitize_splunk_latest(raw: str | None) -> str | None:
    """job export `latest_time`: e.g. now, +0s, -1h, epoch-like tokens."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if len(s) > 64 or ".." in s or "http" in s.lower():
        return None
    sl = s.lower()
    if sl in ("now", "0"):
        return "now" if sl == "now" else "0"
    if re.fullmatch(r"[-+@0-9a-zA-Z._]+", s):
        return s
    return None


def default_studio_earliest() -> str:
    e = (os.environ.get("SPLUNK_SAMSUNG_STUDIO_EARLIEST") or "").strip()
    if e:
        opt = sanitize_splunk_earliest(e)
        if opt:
            return opt
    return "-1d@d"


def default_studio_latest() -> str:
    l = (os.environ.get("SPLUNK_SAMSUNG_STUDIO_LATEST") or "").strip()
    if l:
        opt = sanitize_splunk_latest(l)
        if opt:
            return opt
    return "now"


def _studio_json_candidate() -> Path:
    raw = (os.environ.get("SPLUNK_SAMSUNG_STUDIO_JSON") or "").strip()
    if raw:
        return Path(raw) if Path(raw).is_absolute() else (_BASE / raw)
    return _SPL_DIR / "samsung_studio_dashboard.json"


def _studio_json_path() -> Path | None:
    """Path to a Studio export file if it already exists (legacy helper)."""
    p = _studio_json_candidate()
    return p if p.is_file() else None


def _parse_dashboard_app_and_view() -> tuple[str, str] | None:
    """(app, view) from SPLUNK_DASHBOARD_PATH, e.g. /en-US/app/search/my_dash?tab=1 → search, my_dash."""
    p = (os.environ.get("SPLUNK_DASHBOARD_PATH") or "").strip()
    m = re.search(r"/(?:en-GB|en-US)/app/([^/]+)/([^?/#\s]+)", p, re.I) or re.search(
        r"/app/([^/]+)/([^?/#\s]+)", p, re.I
    )
    if m:
        return m.group(1), m.group(2)
    return None


def _rest_fetch_studio_enabled() -> bool:
    f = (os.environ.get("SPLUNK_FETCH_STUDIO_FROM_REST") or "").strip().lower()
    if f in ("0", "false", "no", "off"):
        return False
    return True


def _json_from_splunk_view_get_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("visualizations"), dict) and isinstance(
        payload.get("dataSources"), dict
    ):
        return payload
    content = (payload.get("entry") or [{}])[0] if payload.get("entry") else None
    if isinstance(content, dict) and "content" in content:
        return _json_from_splunk_view_entry(content.get("content"))
    return None


def _json_from_splunk_view_entry(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    for key in (
        "eai:body",
        "eai:definition",
        "definition",
        "definition:JSON",
    ):
        v = content.get(key)
        if isinstance(v, str) and v.strip().startswith("{"):
            try:
                o = json.loads(v)
            except json.JSONDecodeError:
                o = None
            if isinstance(o, dict) and o.get("visualizations") and o.get("dataSources"):
                return o
    eai = content.get("eai:data")
    if not isinstance(eai, str) or not eai.strip():
        return None
    s = eai
    m = re.search(
        r"<definition>\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*</definition>", s, re.IGNORECASE
    )
    if m:
        inner = m.group(1).strip()
        if inner.startswith("{"):
            try:
                o = json.loads(inner)
            except json.JSONDecodeError:
                o = None
            if isinstance(o, dict) and o.get("visualizations"):
                return o
    m2 = re.search(
        r"<eai:definition><!\[CDATA\[([\s\S]*?)\]\]></eai:definition>", s, re.IGNORECASE
    )
    if m2:
        try:
            o2 = json.loads(m2.group(1).strip())
        except json.JSONDecodeError:
            o2 = None
        if isinstance(o2, dict) and o2.get("visualizations"):
            return o2
    for cblock in re.findall(
        r"<!\[CDATA\[([\s\S]*?)\]\]>", s, flags=re.IGNORECASE
    ):
        t = cblock.strip()
        if not t.startswith("{"):
            continue
        try:
            o3 = json.loads(t)
        except json.JSONDecodeError:
            continue
        if isinstance(o3, dict) and o3.get("visualizations") and o3.get("dataSources"):
            return o3
    return None


_rest_lock = threading.Lock()
_rest_studio: dict[str, Any] | None = None
_rest_studio_error: str | None = None
_rest_studio_fetched_at: float = 0.0
_REST_STUDIO_ERR_TTL = 120.0
_REST_STUDIO_OK_TTL = 3600.0


def _do_fetch_studio_from_rest() -> tuple[dict[str, Any] | None, str | None]:
    host = (os.environ.get("SPLUNK_HOST") or "arlo.splunkcloud.com").strip().rstrip("/")
    port = (os.environ.get("SPLUNK_MGMT_PORT") or "8089").strip() or "8089"
    app_view = _parse_dashboard_app_and_view()
    if not app_view:
        return None, "SPLUNK_DASHBOARD_PATH must include /app/<app>/<view> (e.g. /en-US/app/search/my_dash?…)"
    app, view = app_view
    app = (os.environ.get("SPLUNK_STUDIO_REST_APP") or app).strip()
    view = (os.environ.get("SPLUNK_STUDIO_REST_VIEW") or view).strip()
    owner = (os.environ.get("SPLUNK_NAMESPACE_OWNER") or "nobody").strip() or "nobody"
    url = f"https://{host}:{port}/servicesNS/{owner}/{app}/data/ui/views/{view}"
    t = (os.environ.get("SPLUNK_TOKEN") or "").strip()
    if not t:
        return None, "SPLUNK_TOKEN is missing"
    mode = (os.environ.get("SPLUNK_AUTH_MODE") or "bearer").lower()
    if mode in ("splunk", "splunk_token"):
        h_auth = f"Splunk {t}"
    else:
        h_auth = f"Bearer {t}"
    try:
        r = requests.get(
            url,
            headers={"Authorization": h_auth},
            params={"output_mode": "json"},
            verify=True,
            timeout=_splunk_rest_get_timeout(),
        )
    except requests.RequestException as e:
        return None, f"REST {url}: {e!s}"
    if r.status_code != 200:
        return None, f"REST {view} HTTP {r.status_code}: {r.text[:800]!r}"
    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        return None, f"Non-JSON response: {e!s}"
    inner = _json_from_splunk_view_get_payload(payload)
    if not inner and isinstance(payload.get("entry"), list) and payload.get("entry"):
        e0 = payload["entry"][0]
        if isinstance(e0, dict) and e0.get("content"):
            inner = _json_from_splunk_view_entry(e0.get("content"))
    if inner and isinstance(inner.get("visualizations"), dict):
        return inner, None
    return None, (
        f"Could not extract Dashboard Studio JSON from {view!r} "
        "(classic XML dashboard, not Studio?). Export to spl/samsung_studio_dashboard.json or use a Studio-built dashboard."
    )


def _get_studio_from_rest_cache() -> tuple[dict[str, Any] | None, str | None]:
    global _rest_studio, _rest_studio_error, _rest_studio_fetched_at
    now = time.time()
    with _rest_lock:
        if _rest_studio is not None and (now - _rest_studio_fetched_at) < _REST_STUDIO_OK_TTL:
            return _rest_studio, None
        if (
            _rest_studio_error
            and _rest_studio is None
            and (now - _rest_studio_fetched_at) < _REST_STUDIO_ERR_TTL
        ):
            return None, _rest_studio_error
    d, e = _do_fetch_studio_from_rest()
    with _rest_lock:
        _rest_studio_fetched_at = time.time()
        if d:
            _rest_studio, _rest_studio_error = d, None
        else:
            _rest_studio = None
            _rest_studio_error = e
    return d, e


def _load_studio_definition() -> dict[str, Any] | None:
    d, _e = _resolve_studio_definition()
    return d


def _resolve_studio_definition() -> tuple[dict[str, Any] | None, str | None]:
    """Load: file (if present), else REST to Splunk. Explicit errors → second return value."""
    cp = _studio_json_candidate()
    if cp.is_file():
        try:
            return json.loads(cp.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as ex:
            return None, f"Invalid JSON in {cp}: {ex}"
    if not _rest_fetch_studio_enabled() or not (os.environ.get("SPLUNK_TOKEN") or "").strip():
        return None, None
    if not _parse_dashboard_app_and_view():
        return None, None
    return _get_studio_from_rest_cache()


def _one_line_spl(s: str) -> str:
    t = re.sub(r"[\n\r\t]+", " ", s.strip())
    t = re.sub(r" {2,}", " ", t)
    return t.strip()


def _search_string_for_job_export(s: str) -> str:
    """
    POST /services/search/jobs/export expects SPL that the parser can run as a full search.
    Queries that start with index=... (like Studio exports) are not valid unless prefixed with
    the 'search' command, or Splunk returns: Unknown search command 'index'.
    Pipelines that start with '|' or already with 'search ' are left unchanged.
    """
    one = _one_line_spl(s)
    if not one:
        return one
    t = one.lstrip()
    if t.startswith("|") or t.lower().startswith("search "):
        return one
    return f"search {one}"


def _auth_headers() -> dict[str, str]:
    t = (os.environ.get("SPLUNK_TOKEN") or "").strip()
    if not t:
        return {}
    mode = (os.environ.get("SPLUNK_AUTH_MODE") or "bearer").lower()
    if mode in ("splunk", "splunk_token"):
        h = f"Splunk {t}"
    else:
        h = f"Bearer {t}"
    return {"Authorization": h, "Content-Type": "application/x-www-form-urlencoded"}


def _export_search_parsed(
    name: str,
    search: str,
    host: str,
    earliest: str,
    latest: str,
) -> tuple[str, list[dict[str, Any]] | None, str | None]:
    if not (os.environ.get("SPLUNK_TOKEN") or "").strip() or not (search or "").strip():
        return name, None, "missing token or search"
    headers = _auth_headers()
    try:
        tz = splunk_p0_job_timezone()
    except Exception:
        tz = "America/Los_Angeles"
    url = f"https://{host}:8089/services/search/jobs/export"
    from tools.splunk_tool import splunk_rest_dispatch_form_fields

    data: dict[str, str] = {
        "search": _search_string_for_job_export(search),
        "earliest_time": earliest,
        "latest_time": latest,
        "output_mode": "json",
        **splunk_rest_dispatch_form_fields(),
    }
    if tz:
        data["timezone"] = tz
    tmo = _splunk_export_http_timeout()
    try:
        from tools.splunk_tool import splunk_ipv4_rest_scope

        with splunk_ipv4_rest_scope():
            r = requests.post(url, headers=headers, data=data, verify=True, timeout=tmo)
            if r.status_code == 400 and "timezone" in data:
                d2 = {k: v for k, v in data.items() if k != "timezone"}
                r = requests.post(url, headers=headers, data=d2, verify=True, timeout=tmo)
        if r.status_code != 200:
            return name, None, f"HTTP {r.status_code}: {r.text[:500]!r}"
        out: list[dict[str, Any]] = []
        for line in r.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not obj.get("result"):
                continue
            # Only skip preview rows; many exports omit "preview" on final rows (treating
            # missing as non-preview). Requiring `is False` dropped all rows before.
            if obj.get("preview") is True:
                continue
            res = obj.get("result")
            if isinstance(res, list) and res:
                if all(
                    isinstance(x, dict) and "name" in x and "value" in x for x in res
                ):
                    res = {str(d["name"]): d.get("value") for d in res}
                else:
                    continue
            if not isinstance(res, dict):
                continue
            out.append(res)
        if not out:
            return name, None, "No result rows in export (empty search, no data, or only preview; check SPL and time range)."
        return name, out, None
    except requests.RequestException as e:
        return name, None, str(e)


def _panel_spl_for_env(env: str) -> tuple[str, str, str]:
    """Return (id, how, search_string)."""
    envu = env.upper()
    e_key = f"SPLUNK_SAMSUNG_SPL_{envu}"
    env_raw = (os.environ.get(e_key) or "").strip()
    if env_raw.startswith("file:"):
        rel = env_raw[5:].strip()
        fpath = (Path(rel) if Path(rel).is_absolute() else _BASE / rel).resolve()
        if fpath.is_file():
            return env, f"file:{fpath.name}", fpath.read_text(encoding="utf-8").strip()
    elif env_raw and not env_raw.lower().startswith("file:"):
        return env, f"env:{e_key}", env_raw
    fpath2 = _SPL_DIR / f"samsung_{env}.spl"
    if fpath2.is_file():
        return env, f"file:{fpath2.name}", fpath2.read_text(encoding="utf-8").strip()
    s_key = f"SPLUNK_SAMSUNG_SAVED_{envu}"
    sname = (os.environ.get(s_key) or "").strip()
    if sname:
        s_esc = sname.replace("\\", "\\\\").replace('"', '\\"')
        return env, f"saved:{sname}", f'| savedsearch "{s_esc}"'
    return env, "", ""


def any_panel_configured() -> bool:
    if _studio_json_path() is not None:
        return True
    for env in ("prod", "qa", "dev"):
        *_, s = _panel_spl_for_env(env)
        if s:
            return True
    if (os.environ.get("SPLUNK_TOKEN") or "").strip() and _rest_fetch_studio_enabled():
        if _parse_dashboard_app_and_view() is not None:
            return True
    return False


def _float_cell(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v)
        return x if math.isfinite(x) else None
    try:
        x = float(str(v).strip().replace(",", ""))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _normalize_splunk_epoch_to_seconds(raw: float) -> float:
    """
    Search/job JSON often sends Unix time as **seconds** (1.7e9) but some exports use
    **milliseconds** (1.7e12) or more. fromtimestamp and chart labels expect **seconds** in UTC.
    Heuristic: true epoch seconds in the 2000s–2100s stay under ~4e9; values ≫ 1e11 are not
    plausibly seconds, so we rescale in steps of 1000ms / 1000us / …
    """
    if not math.isfinite(raw):
        return raw
    t = float(raw)
    n = 0
    # Loosen upper bound: anything over ~year 5138 in "seconds" is a higher-resolution clock.
    while abs(t) > 1e11 and n < 4:
        t /= 1000.0
        n += 1
    return t


def _splunk_time_to_epoch(tv: Any, naive_wall_timezone: str | None = None) -> float | None:
    """
    Parse Splunk job export _time / time: epoch (number or string), or ISO-8601 strings.
    Naive ISO wall-clock values use Splunk job TZ (US Pacific by default), not UTC.
    """
    if tv is None:
        return None
    if isinstance(tv, list) and len(tv) > 0:
        tv = tv[0]
    tv = _coerce_raw_time_value(tv)
    if tv is None:
        return None
    wall = naive_wall_timezone or splunk_p0_job_timezone()
    parsed = _splunk_row_epoch_seconds(tv, naive_wall_timezone=wall)
    if parsed is not None:
        return parsed
    if isinstance(tv, bool):
        return None
    if isinstance(tv, (int, float)):
        x = float(tv)
        if not math.isfinite(x):
            return None
        return _normalize_splunk_epoch_to_seconds(x)
    s = str(tv).strip()
    if not s:
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if re.fullmatch(r"(19|20|21)[0-9][0-9]", s) is not None:
        return None
    return _normalize_splunk_epoch_to_seconds(x)


def _coerce_raw_time_value(tv: Any) -> Any:
    """
    Splunk JSON export may quote times, or nest values; strip noise before parsing.
    """
    if tv is None:
        return None
    if isinstance(tv, str):
        t = tv.strip()
        if len(t) >= 2 and t[0] in '"\'' and t[0] == t[-1]:
            t = t[1:-1]
        t = t.strip()
        if t.lower() in ("", "null", "none"):
            return None
        return t
    if isinstance(tv, dict):
        for sub in ("value", "#text", "_value"):
            if sub in tv:
                return _coerce_raw_time_value(tv[sub])
        if len(tv) == 1:
            return _coerce_raw_time_value(next(iter(tv.values())))
    return tv


def _discover_splunk_time_key(rows: list[dict[str, Any]]) -> str | None:
    """
    Find which result column holds a parseable time. REST /export sometimes uses
    only 'time' or a renamed field; Studio stats usually keep '_time' but not always.
    """
    if not rows:
        return None
    sample = rows[: min(40, len(rows))]
    override = (os.environ.get("SPLUNK_CHART_TIME_FIELD") or os.environ.get("SPLUNK_TIME_FIELD") or "").strip()
    if override and override in rows[0]:
        n = sum(
            1
            for r in sample
            if _splunk_time_to_epoch(_coerce_raw_time_value(r.get(override))) is not None
        )
        if n > 0:
            return override
    for k in ("_time", "time", "Time", "ltime", "timestamp", "_ts"):
        if k not in rows[0]:
            continue
        n = sum(
            1
            for r in sample
            if _splunk_time_to_epoch(_coerce_raw_time_value(r.get(k))) is not None
        )
        if n > 0:
            return k
    for k in rows[0].keys():
        if not k or k in ("host", "source", "sourcetype", "linecount"):
            continue
        kl = k.lower()
        if "time" not in kl and not kl.endswith("_ts"):
            continue
        n = sum(
            1
            for r in sample
            if _splunk_time_to_epoch(_coerce_raw_time_value(r.get(k))) is not None
        )
        if n > 0:
            return k
    return None


def _chart_fields(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    from collections import Counter

    skip = {"_time", "time", "host", "source", "sourcetype", "linecount", "hour"}
    keys: Counter[str] = Counter()
    for r in rows:
        for k, v in r.items():
            if not k or k in skip:
                continue
            if k.startswith("_"):
                continue
            if "raw" in k.lower():
                continue
            if _float_cell(v) is None:
                continue
            keys[k] += 1
    preferred: list[str] = []
    for pat in (r"p(50|95|99)", r"^avg$", r"^average$", r"percentile", r"latency", r"seconds"):
        for k in list(keys.keys()):
            if re.search(pat, k, re.I) and k not in preferred:
                preferred.append(k)
    rest = [k for k, _ in keys.most_common() if k not in preferred]
    out = preferred + [k for k in rest if k not in preferred]
    return out[:8]


def _ordered_p50_p99_keys(candidates: list[str]) -> list[str]:
    """Order series like the Studio dashboard: P50, P95, P99, Avg, then the rest."""

    def _rank(k: str) -> int:
        s = (k or "").strip()
        s_low = s.lower()
        if s_low.startswith("p50") or s_low.startswith('"p50'):
            return 0
        if s_low.startswith("p95"):
            return 1
        if s_low.startswith("p99"):
            return 2
        if s_low.startswith("avg") or "average" in s_low:
            return 3
        return 50

    cset = list(dict.fromkeys(candidates))
    cset.sort(key=lambda k: (_rank(k), k.lower()))
    return cset[:12]


def _is_p50_p95_p99_or_avg_series_key(k: str) -> bool:
    """
    Only P50, P95, P99, and Avg/ Average / mean for chart lines (excludes e.g. hour, count).

    Studio SPL uses aliased names like "P50 (s)", "Avg (s)"; after lower() these are
    "p50 (s)", "avg (s)" — not equal to the bare token "avg", so we must match prefixes.
    """
    if not k:
        return False
    t = (k or "").strip().lower()
    t = t.strip('\'"')
    if t in ("avg", "average", "mean"):
        return True
    if t == "hour":
        return False
    if t.startswith("p50") or t.startswith("p95") or t.startswith("p99"):
        return True
    if t.startswith("avg") or t.startswith("average") or t.startswith("mean"):
        return True
    return bool(re.match(r"^p(50|95|99)\b", t))


def _filter_series_to_p50_p95_p99_avg(candidates: list[str]) -> list[str]:
    """Keep at most the four metric series, in Studio order (P50 → P95 → P99 → Avg)."""
    f = [k for k in candidates if _is_p50_p95_p99_or_avg_series_key(k)]
    if not f:
        return []
    return _ordered_p50_p99_keys(f)[:4]


def _rows_to_labels_series(
    rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[float | None]], str | None]:
    if not rows:
        return [], {}, None
    # Hour-of-day panels: stats by "hour" without a parseable time column
    time_key = _discover_splunk_time_key(rows)
    has_time = time_key is not None
    has_hour = any(r.get("hour") is not None for r in rows)
    if has_hour and not has_time:
        def _hk(r: dict[str, Any]) -> int:
            try:
                return int(str(r.get("hour")).strip())
            except (TypeError, ValueError):
                return 0

        rows_sorted = sorted(rows, key=_hk)
        labels = [str(r.get("hour")) for r in rows_sorted]
        cand = [k for k in rows_sorted[0].keys() if k and k != "hour"]
        raw_f = _chart_fields(rows_sorted) or cand
        fields = _filter_series_to_p50_p95_p99_avg(raw_f)
        if not fields:
            return labels, {}, "No numeric columns beside hour in results."
        ser: dict[str, list[float | None]] = {f: [] for f in fields}
        for r in rows_sorted:
            for f in fields:
                ser[f].append(_float_cell(r.get(f)))
        return labels, ser, None

    if not time_key:
        r0 = rows[0]
        keys = ", ".join(sorted(r0.keys())[:28])
        sample = {k: r0.get(k) for k in list(r0.keys())[:10]}
        return (
            [],
            {},
            "No time column with parseable values. "
            f"Keys: {keys}. First-row sample: {escape(repr(sample)[:320])}. "
            "In Splunk, use timechart or | bin _time / stats ... by _time. "
            "If the field is custom, set SPLUNK_CHART_TIME_FIELD in .env.",
        )

    dtz = splunk_p0_job_timezone() or splunk_display_timezone() or "America/Los_Angeles"
    try:
        tz = ZoneInfo(dtz)
    except Exception:
        try:
            tz = ZoneInfo("America/Los_Angeles")
        except Exception:
            tz = None
    parsed: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        tv = _coerce_raw_time_value(r.get(time_key))
        ts = _splunk_time_to_epoch(tv, naive_wall_timezone=dtz)
        if ts is None or not math.isfinite(ts):
            continue
        parsed.append((ts, r))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        v0 = _coerce_raw_time_value(rows[0].get(time_key)) if rows else None
        return (
            [],
            {},
            f"No valid values in {time_key!r} after parsing. "
            f"First value: {escape(repr(v0)[:120])}. Check Splunk strftime/epoch format.",
        )

    labels: list[str] = []
    for ts, _ in parsed:
        if tz is not None:
            try:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(tz)
                labels.append(dt.strftime("%Y-%m-%d %H:%M"))
            except (OverflowError, OSError, ValueError):
                labels.append(str(ts))
        else:
            labels.append(str(int(ts)))
    sample_rows = [p[1] for p in parsed]
    raw_fields = _chart_fields(sample_rows)
    fields = _filter_series_to_p50_p95_p99_avg(raw_fields) if raw_fields else []
    if not fields:
        return labels, {}, "No numeric series columns; ensure search returns _time and metrics (P50, etc.)."
    ser: dict[str, list[float | None]] = {f: [] for f in fields}
    for _ts, r in parsed:
        for f in fields:
            ser[f].append(_float_cell(r.get(f)))
    return labels, ser, None


_COLORS = SPLUNK_STUDIO_DEFAULT_COLORS + ["#a78bfa", "#f472b6", "#2dd4bf", "#c4b5fd"]


def _studio_items_in_order(d: dict[str, Any]) -> list[dict[str, Any]]:
    vis = d.get("visualizations") or {}
    dss = d.get("dataSources") or {}
    ldefs = (d.get("layout") or {}).get("layoutDefinitions") or {}
    struct: list | None = None
    tabs = (d.get("layout") or {}).get("tabs") or {}
    tab_items = tabs.get("items") if isinstance(tabs, dict) else None
    preferred = None
    if isinstance(tab_items, list) and tab_items:
        t0 = tab_items[0]
        if isinstance(t0, dict):
            preferred = (t0.get("layoutId") or "").strip() or None
    if preferred and isinstance(ldefs.get(preferred), dict):
        struct = ldefs[preferred].get("structure")
    if not struct:
        for _lid, ldef in ldefs.items():
            if isinstance(ldef, dict) and ldef.get("structure"):
                struct = ldef["structure"]
                break
    if not struct:
        return []
    out: list[dict[str, Any]] = []
    for block in struct:
        item = block.get("item")
        if not item:
            continue
        v = vis.get(item) or {}
        vt = v.get("type") or ""
        if vt == "splunk.markdown":
            md = (v.get("options") or {}).get("markdown") or ""
            out.append({"kind": "section", "markdown": md})
        elif vt == "splunk.line":
            dsid = (v.get("dataSources") or {}).get("primary")
            q = ((dss.get(dsid) or {}).get("options") or {}).get("query")
            if not q or not str(q).strip():
                continue
            opt = v.get("options") or {}
            out.append(
                {
                    "kind": "chart",
                    "id": item,
                    "title": v.get("title") or item,
                    "description": (v.get("description") or "").strip(),
                    "query": str(q),
                    "seriesColors": opt.get("seriesColors") or SPLUNK_STUDIO_DEFAULT_COLORS,
                    "yAxisTitle": (opt.get("yAxisTitleText") or "Latency (s)").strip(),
                    "xAxisTitle": (opt.get("xAxisTitleText") or "Time").strip(),
                }
            )
    return out


def _chart_block_from_result(ch: dict[str, Any], _key: str, rows: list[dict[str, Any]] | None, err: str | None) -> str:
    cid = ch["id"]
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", cid).strip("_") or "c"
    tit = str(ch.get("title") or cid)
    desc = (ch.get("description") or "").strip()
    if err or rows is None:
        return (
            f'<div class="panel-viz" id="{escape(cid)}">'
            f'<h3 class="viz-tit">{escape(tit)}</h3>'
            f'<p class="err">{escape(err or "error")}</p></div>'
        )
    labels, series, serr = _rows_to_labels_series(rows)
    if serr or not series:
        return (
            f'<div class="panel-viz" id="{escape(cid)}">'
            f'<h3 class="viz-tit">{escape(tit)}</h3>'
            f'<p class="err">{escape(serr or "no series")}</p></div>'
        )
    sc = list(ch.get("seriesColors") or SPLUNK_STUDIO_DEFAULT_COLORS)
    colors = {k: sc[i % len(sc)] for i, k in enumerate(series.keys())}
    payload = {
        "labels": labels,
        "series": {k: v for k, v in series.items()},
        "colors": colors,
        "yAxisTitle": ch.get("yAxisTitle") or "Latency (s)",
        "xAxisTitle": ch.get("xAxisTitle") or "Time",
    }
    p64 = base64.b64encode(json.dumps(payload, ensure_ascii=True).encode("utf-8")).decode("ascii")
    return f"""<div class="panel-viz" id="{escape(cid)}">
  <h3 class="viz-tit">{escape(tit)}</h3>
  <div class="canwrap canwrap--studio"><canvas id="cv_{escape(safe)}" data-p64="{escape(p64)}"></canvas></div>
</div>"""


def _build_studio_html(
    _definition: dict[str, Any],
    items: list[dict[str, Any]],
    host: str,
    studio_earliest: str | None = None,
    studio_latest: str | None = None,
) -> str:
    est = (sanitize_splunk_earliest(studio_earliest) if studio_earliest else None) or default_studio_earliest()
    latest = (sanitize_splunk_latest(studio_latest) if studio_latest else None) or default_studio_latest()
    charts = [x for x in items if x.get("kind") == "chart"]
    if not charts:
        return "<p class=err>No splunk.line panels with a query in the Studio JSON.</p>"

    by_id: dict[str, tuple[str, list[dict[str, Any]] | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=min(24, max(1, len(charts)))) as ex:
        fmap = {ex.submit(_export_search_parsed, c["id"], c["query"], host, est, latest): c for c in charts}
        for fut in as_completed(fmap):
            c = fmap[fut]
            by_id[c["id"]] = fut.result()

    out: list[str] = []
    for it in items:
        if it.get("kind") == "section":
            # Omit Studio markdown section headers in iframe embed (keep charts only)
            continue
        elif it.get("kind") == "chart":
            cid = it["id"]
            tr = by_id.get(cid, ("", None, "search not run"))
            out.append(_chart_block_from_result(it, tr[0], tr[1], tr[2]))
    return "\n".join(out)


def _embed_chart_script() -> str:
    return r"""
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script>
    (function(){
      document.querySelectorAll("canvas[data-p64]").forEach(function(cv){
        try {
          var p64 = cv.getAttribute("data-p64");
          if (!p64) return;
          var p = JSON.parse(atob(p64));
          var labels = p.labels || [];
          var series = p.series || {};
          var colors = p.colors || {};
          var yT = p.yAxisTitle || "";
          var xT = p.xAxisTitle || "";
          var ds = Object.keys(series).map(function(k) {
            var c = colors[k] || "#53a051";
            return {
              label: k, data: (series[k]||[]).map(function(x){ return x==null?null:Number(x); }),
              borderColor: c, backgroundColor: "transparent", pointRadius: 0, borderWidth: 1.6, fill: false, tension: 0.18
            };
          });
          new Chart(cv, { type: "line", data: { labels: labels, datasets: ds },
            options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
              plugins: { legend: { position: "top", labels: { color: "#c4c8de", font: { size: 11 }, boxWidth: 10, padding: 6 } } },
              scales: {
                x: {
                  title: { display: xT.length > 0, text: xT, color: "#7c8298", font: { size: 10 } },
                  ticks: { color: "#6b7090", maxRotation: 45, font: { size: 9 } },
                  grid: { color: "rgba(255,255,255,0.06)" }
                },
                y: {
                  title: { display: yT.length > 0, text: yT, color: "#7c8298", font: { size: 10 } },
                  beginAtZero: true, ticks: { color: "#6b7090" },
                  grid: { color: "rgba(255,255,255,0.06)" }
                }
              } } });
        } catch (e) { console.error(e); }
      });
    })();
    </script>
    """


def _build_legacy_panels_html(host: str, earliest: str, latest: str) -> str:
    work: list[tuple[str, str, str, str]] = []
    for env, title in (("prod", "PROD"), ("qa", "QA"), ("dev", "DEV")):
        eid, how, spl = _panel_spl_for_env(env)
        if spl:
            work.append((eid, title, how, spl))

    if not work:
        u = (os.environ.get("SPLUNK_WEB_BASE") or "https://arlo.splunkcloud.com").rstrip("/")
        p = (os.environ.get("SPLUNK_DASHBOARD_PATH") or "/en-US/app/search/samsung_alarm_latencies?tab=layout_1")
        return (
            "<p class=muted>Define <code>spl/samsung_studio_dashboard.json</code> (Studio export) or, in classic mode, "
            "<code>SPLUNK_SAMSUNG_SAVED_PROD</code> / <code>spl/samsung_prod.spl</code> (and qa/dev). "
            f'For a full Studio dashboard (all line panels from layout), copy e.g. spl/samsung_studio_dashboard.json. '
            f'<a class=lnk target="_blank" rel="noreferrer" href="{escape(u + p)}">Open Splunk UI</a>.</p>'
        )

    out_parts: list[str] = []
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for eid, title, how, spl in work:
            fut = ex.submit(_export_search_parsed, f"{eid}::{title}", spl, host, earliest, latest)
            futures[fut] = (eid, title, how, spl)
        for fut in as_completed(futures):
            eid, title, how, spl = futures[fut]
            _key, rows, err = fut.result()
            n = len(rows) if rows else 0
            ch = {
                "id": f"legacy_{eid}",
                "title": f"Environment: {title}",
                "description": f"Source: {how} · rows {n}",
                "seriesColors": SPLUNK_STUDIO_DEFAULT_COLORS,
                "yAxisTitle": "Latency (s)",
                "xAxisTitle": "Time",
            }
            out_parts.append(_chart_block_from_result(ch, _key, rows, err))
    return "\n".join(out_parts)


def _build_embed_html_body(
    hours: int,
    studio_earliest: str | None = None,
    studio_latest: str | None = None,
) -> str:
    host = (os.environ.get("SPLUNK_HOST") or "arlo.splunkcloud.com").strip().rstrip("/")
    if not (os.environ.get("SPLUNK_TOKEN") or "").strip():
        return (
            "<p class=err>Configure <code>SPLUNK_TOKEN</code> for REST (port 8089). "
            "No browser login to Splunk is used.</p>"
        )
    st, serr = _resolve_studio_definition()
    if serr and not st:
        return f'<p class=err>Studio definition: {escape(serr)}</p>' + _embed_chart_script()
    if st:
        items = _studio_items_in_order(st)
        if items and any(x.get("kind") == "chart" for x in items):
            return (
                _build_studio_html(
                    st,
                    items,
                    host,
                    studio_earliest=studio_earliest,
                    studio_latest=studio_latest,
                )
                + _embed_chart_script()
            )

    se = sanitize_splunk_earliest(studio_earliest) if studio_earliest else None
    earliest = se or f"-{int(hours)}h@h"
    sl = sanitize_splunk_latest(studio_latest) if studio_latest else None
    latest = sl or "now"
    return _build_legacy_panels_html(host, earliest, latest) + _embed_chart_script()


def build_embed_document(
    hours: int,
    studio_earliest: str | None = None,
    studio_latest: str | None = None,
) -> str:
    h = max(1, int(hours))
    st, _serr = _resolve_studio_definition()
    page_title = (st or {}).get("title") or "Samsung — alarm creation latencies (API)"
    se = (
        studio_earliest
        if studio_earliest and sanitize_splunk_earliest(studio_earliest)
        else None
    )
    sl = (
        studio_latest
        if studio_latest and sanitize_splunk_latest(studio_latest)
        else None
    )
    inner = _build_embed_html_body(h, studio_earliest=se, studio_latest=sl)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{escape(page_title)}</title>
<style>
body {{ font-family: system-ui, "Segoe UI", Roboto, sans-serif; background: #0b0c12; color: #e6e7ee; margin: 0; padding: 8px 10px 20px; max-width: 100%; }}
p.muted {{ color: #6b7290; font-size: 0.82rem; line-height: 1.4; max-width: 80ch; }}
p.muted.small {{ font-size: 0.75rem; margin: 0 0 8px 0; max-width: 95ch; line-height: 1.4; }}
p.err {{ color: #f87171; font-size: 0.86rem; }}
a.lnk {{ color: #93c5fd; }}
ul.muted {{ color: #6b7290; font-size: 0.82rem; line-height: 1.5; max-width: 80ch; }}
code {{ font-size: 0.85em; color: #a5b4fc; word-break: break-all; }}
.panel-viz {{ background: #10121a; border: 1px solid #1e2235; border-radius: 8px; padding: 12px 14px 16px; margin: 0 0 16px 0; }}
.viz-tit {{ font-size: 0.88rem; font-weight: 600; color: #dce0f0; margin: 0 0 4px 0; line-height: 1.3; }}
.canwrap {{ position: relative; height: 260px; max-width: 100%; margin-top: 6px; }}
.canwrap--studio {{ height: 280px; margin-top: 8px; }}
</style>
</head><body>
{inner}
</body></html>"""


def build_embed_for_flask(
    hours: int,
    studio_earliest: str | None = None,
    studio_latest: str | None = None,
):
    from flask import Response

    se = (
        studio_earliest
        if studio_earliest and sanitize_splunk_earliest(studio_earliest)
        else None
    )
    sl = (
        studio_latest
        if studio_latest and sanitize_splunk_latest(studio_latest)
        else None
    )
    return Response(
        build_embed_document(hours, studio_earliest=se, studio_latest=sl),
        mimetype="text/html; charset=utf-8",
    )
