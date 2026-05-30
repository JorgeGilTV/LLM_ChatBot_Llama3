import os
import re
import html
import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import mean, pstdev

import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()


def splunk_search_timezone() -> str:
    """IANA zone for REST jobs (earliest/latest + predict). Default US Pacific."""
    return (os.getenv("SPLUNK_SEARCH_TIMEZONE") or "America/Los_Angeles").strip()


def splunk_display_timezone() -> str:
    """Labels / UI (same default as search unless overridden)."""
    return (os.getenv("SPLUNK_DISPLAY_TIMEZONE") or splunk_search_timezone()).strip()


def _splunk_resolve_p0_timezone_id(raw: str) -> str:
    """
    Normalize env values like PST/PDT/Pacific to a valid IANA id for Splunk REST + Python.
    America/Los_Angeles follows US Pacific (PST winter / PDT summer).
    """
    if not raw:
        return "America/Los_Angeles"
    key = raw.strip().lower()
    if key in ("pst", "pdt", "pt", "pacific", "us/pacific", "us-pacific", "pacific time"):
        return "America/Los_Angeles"
    candidate = raw.strip()
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return "America/Los_Angeles"


def splunk_p0_job_timezone() -> str:
    """
    Pacific-aligned TZ for P0 Splunk REST jobs, bucket boundaries, chart labels, and outliers.

    Default ``America/Los_Angeles`` (US Pacific: PST in winter, PDT in summer — same wall clock
    family Splunk UI uses for Pacific searches). Override with ``SPLUNK_P0_TIMEZONE`` (IANA or
    aliases pst/pdt/pacific), else ``SPLUNK_SEARCH_TIMEZONE``.
    """
    raw = (os.getenv("SPLUNK_P0_TIMEZONE") or os.getenv("SPLUNK_SEARCH_TIMEZONE") or "America/Los_Angeles").strip()
    return _splunk_resolve_p0_timezone_id(raw or "America/Los_Angeles")


def splunk_mgmt_port() -> str:
    """Splunk management REST port (Splunk Cloud default 8089)."""
    return (os.getenv("SPLUNK_MGMT_PORT") or "8089").strip() or "8089"


def splunk_rest_auto_cancel_enabled() -> bool:
    """
    Splunk REST ``auto_cancel``: when enabled (Splunk default), dispatching a new search
    cancels a prior job with the same search name. Default off so parallel/chunked jobs
    are not dropped.
    """
    raw = (os.getenv("SPLUNK_AUTO_CANCEL") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def splunk_rest_dispatch_form_fields() -> dict[str, str]:
    """Extra POST fields for /services/search/jobs and /export (merged into form body)."""
    return {"auto_cancel": "1" if splunk_rest_auto_cancel_enabled() else "0"}


def splunk_rest_timeouts() -> tuple[int, int]:
    """
    (connect_seconds, read_seconds) for Splunk REST export.
    Connect timeout = TCP handshake to management port — if this fires, traffic often never reaches Splunk
    (firewall, VPN, or Splunk Cloud IP allowlist), not a slow query.
    """
    try:
        c = int((os.getenv("SPLUNK_CONNECT_TIMEOUT") or "30").strip())
    except ValueError:
        c = 30
    try:
        r = int((os.getenv("SPLUNK_READ_TIMEOUT") or "180").strip())
    except ValueError:
        r = 180
    return max(5, min(c, 300)), max(30, min(r, 3600))


_orig_getaddrinfo = socket.getaddrinfo
_splunk_dns_tls = threading.local()
_splunk_gai_ipv4_patched = False


def splunk_prefer_ipv4() -> bool:
    """
    Default True: Splunk REST resolves and connects over IPv4 only (Splunk Cloud allowlists are often IPv4-only).

    Set SPLUNK_PREFER_IPV4=0 (or false/no/off) to use the system default (dual-stack / IPv6).
    SPLUNK_FORCE_IPV4 is an alias when SPLUNK_PREFER_IPV4 is unset.
    """
    p = (os.getenv("SPLUNK_PREFER_IPV4") or "").strip().lower()
    if p in ("0", "false", "no", "off"):
        return False
    if p in ("1", "true", "yes", "on"):
        return True
    f = (os.getenv("SPLUNK_FORCE_IPV4") or "").strip().lower()
    if f in ("0", "false", "no", "off"):
        return False
    if f in ("1", "true", "yes", "on"):
        return True
    return True


def _splunk_getaddrinfo_ipv4_wrapper(*args, **kwargs):
    if not getattr(_splunk_dns_tls, "force_af_inet", False):
        return _orig_getaddrinfo(*args, **kwargs)
    kw = dict(kwargs)
    lst = list(args)
    nargs = len(lst)
    if nargs == 2:
        h, p = lst
        return _orig_getaddrinfo(
            h,
            p,
            socket.AF_INET,
            kw.pop("type", 0),
            kw.pop("proto", 0),
            kw.pop("flags", 0),
            **kw,
        )
    if nargs >= 3 and lst[2] in (0, socket.AF_UNSPEC):
        lst[2] = socket.AF_INET
    if "family" in kw and kw["family"] in (0, socket.AF_UNSPEC):
        kw["family"] = socket.AF_INET
    return _orig_getaddrinfo(*lst, **kw)


def _ensure_splunk_ipv4_getaddrinfo_patch() -> None:
    global _splunk_gai_ipv4_patched
    if _splunk_gai_ipv4_patched:
        return
    if not splunk_prefer_ipv4():
        return
    socket.getaddrinfo = _splunk_getaddrinfo_ipv4_wrapper
    _splunk_gai_ipv4_patched = True


@contextmanager
def splunk_ipv4_rest_scope():
    """Restrict DNS resolution for Splunk REST to IPv4 on this thread (default on; opt out with SPLUNK_PREFER_IPV4=0)."""
    if not splunk_prefer_ipv4():
        yield
        return
    _ensure_splunk_ipv4_getaddrinfo_patch()
    prev = getattr(_splunk_dns_tls, "force_af_inet", False)
    _splunk_dns_tls.force_af_inet = True
    try:
        yield
    finally:
        _splunk_dns_tls.force_af_inet = prev


def _splunk_rest_authorization_value(splunk_token: str) -> str:
    """
    Build Authorization header value for Splunk REST.
    SPLUNK_AUTH_MODE or SPLUNK_REST_AUTH: bearer (default) | splunk
    Use splunk for classic session tokens (Authorization: Splunk <token>).
    """
    mode = (os.getenv("SPLUNK_AUTH_MODE") or os.getenv("SPLUNK_REST_AUTH") or "bearer").strip().lower()
    if mode in ("splunk", "session", "splunk-session"):
        return f"Splunk {splunk_token}"
    return f"Bearer {splunk_token}"


def execute_splunk_query(
    query_key,
    query_data,
    splunk_host,
    splunk_token,
    earliest_time,
    latest_time,
    timezone=None,
):
    """Execute a single Splunk query - helper for parallel execution"""
    port = splunk_mgmt_port()
    connect_s, read_s = splunk_rest_timeouts()
    try:
        with splunk_ipv4_rest_scope():
            search_url = f"https://{splunk_host}:{port}/services/search/jobs/export"
            to = (connect_s, read_s)
            tz = timezone if timezone is not None else splunk_search_timezone()
            data = {
                "search": query_data,
                "earliest_time": earliest_time,
                "latest_time": latest_time,
                "output_mode": "json",
                **splunk_rest_dispatch_form_fields(),
            }
            if tz:
                data["timezone"] = tz

            def _post_with_auth(auth_header_value: str):
                headers = {
                    "Authorization": auth_header_value,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                resp = requests.post(search_url, headers=headers, data=data, verify=True, timeout=to)
                if resp.status_code == 400 and tz:
                    body_low = (resp.text or "").lower()
                    if any(
                        x in body_low
                        for x in ("timezone", "time zone", "invalid time", "unrecognized argument")
                    ):
                        data_retry = {k: v for k, v in data.items() if k != "timezone"}
                        resp = requests.post(
                            search_url, headers=headers, data=data_retry, verify=True, timeout=to
                        )
                return resp

            primary = _splunk_rest_authorization_value(splunk_token)
            response = _post_with_auth(primary)

            fb = (os.getenv("SPLUNK_AUTH_401_FALLBACK", "1").strip().lower() not in ("0", "false", "no"))
            if response.status_code == 401 and fb and splunk_token:
                alt = (
                    f"Splunk {splunk_token}"
                    if primary.startswith("Bearer ")
                    else f"Bearer {splunk_token}"
                )
                if alt != primary:
                    response = _post_with_auth(alt)

            if response.status_code == 200:
                # Parse JSON lines from export (NDJSON). Splunk often omits "preview" on final rows;
                # requiring preview==False dropped all rows — only skip streaming preview rows.
                results = []
                for line in response.text.strip().split("\n"):
                    if line:
                        try:
                            result = json.loads(line)
                            row = result.get("result")
                            if not row:
                                continue
                            if result.get("preview") is True:
                                continue
                            results.append(row)
                        except json.JSONDecodeError:
                            continue
                return query_key, results, None
            else:
                return query_key, None, f"HTTP {response.status_code}: {response.text[:200]}"

    except requests.exceptions.ConnectTimeout as e:
        return (
            query_key,
            None,
            "⚠️ TCP connect timeout to "
            f"https://{splunk_host}:{port}/services/search/jobs/export "
            f"({connect_s}s) — the session never reached Splunk on port {port}. "
            "Common causes: (1) corporate network or Wi‑Fi blocks outbound "
            f"{port}; (2) Splunk Cloud IP allowlist does not include this machine's public egress IP; "
            "(3) VPN required for management API. "
            "Splunk REST uses IPv4 by default; set SPLUNK_PREFER_IPV4=0 only if you need dual-stack/IPv6. "
            f"Quick check from this host: `nc -vz {splunk_host} {port}` or open the URL in a browser (expect TLS). "
            f"Detail: {e}",
        )
    except requests.exceptions.Timeout as e:
        return query_key, None, f"⏱️ Request timeout - Splunk query took too long: {str(e)}"
    except requests.exceptions.ConnectionError as e:
        p = splunk_mgmt_port()
        return query_key, None, f"🔌 Connection error — check port {p}, VPN, or firewall: {str(e)}"
    except Exception as e:
        return query_key, None, f"❌ Unexpected error: {str(e)}"


def execute_splunk_queries_parallel(
    queries_dict,
    splunk_host,
    splunk_token,
    earliest_time,
    latest_time,
    max_workers=3,
    timezone=None,
    errors_out: dict | None = None,
):
    """Execute multiple Splunk queries in parallel"""
    results = {}
    
    if not queries_dict:
        return results
    
    print(f"🚀 Executing {len(queries_dict)} Splunk queries in parallel...")
    start_time = time.time()
    tz = timezone if timezone is not None else splunk_search_timezone()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {}
        for query_key, query_data in queries_dict.items():
            future = executor.submit(
                execute_splunk_query,
                query_key,
                query_data,
                splunk_host,
                splunk_token,
                earliest_time,
                latest_time,
                tz,
            )
            future_to_key[future] = query_key
        
        for future in as_completed(future_to_key):
            query_key = future_to_key[future]
            try:
                key, data, error = future.result()
                if error:
                    print(f"❌ Query '{key}' failed: {error}")
                    results[key] = None
                    if errors_out is not None:
                        errors_out[key] = error
                else:
                    results[key] = data
                    print(f"✅ Query '{key}' completed: {len(data) if data else 0} results")
            except Exception as e:
                print(f"❌ Query '{query_key}' exception: {str(e)}")
                results[query_key] = None
                if errors_out is not None:
                    errors_out[query_key] = str(e)
    
    elapsed = time.time() - start_time
    print(f"✅ All Splunk queries completed in {elapsed:.2f}s")
    
    return results


def _splunk_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _splunk_row_epoch_seconds(tr, naive_wall_timezone: str | None = None) -> float | None:
    """
    Splunk export sometimes returns _time as epoch float/string; other builds use ISO text.
    If we drop all rows here, charts show empty even when Splunk returned data.

    ISO timestamps **without** a zone are interpreted in ``naive_wall_timezone`` (Splunk search
    wall clock / REST ``timezone``), not UTC — assuming UTC used to scramble bucket order vs the
    rolling band and inflated false outliers on P0 charts.
    """
    if tr is None or tr == "":
        return None
    if isinstance(tr, (int, float)):
        return float(tr)
    s = str(tr).strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    try:
        s2 = s.replace("Z", "+00:00")
        if s2.endswith(" GMT"):
            s2 = s2[:-4].strip()
        elif s2.endswith(" UTC"):
            s2 = s2[:-4].strip()
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            wall = naive_wall_timezone or "America/Los_Angeles"
            try:
                zi = ZoneInfo(wall)
            except Exception:
                zi = ZoneInfo("America/Los_Angeles")
            dt = dt.replace(tzinfo=zi)
        return dt.timestamp()
    except (TypeError, ValueError, OSError):
        return None


def splunk_p0_streaming_index() -> str:
    """Index for P0 predict pipeline (override if Splunk admins rename the index)."""
    return (os.getenv("SPLUNK_P0_STREAMING_INDEX") or "streaming_prod").strip() or "streaming_prod"


def splunk_web_base_url() -> str:
    """Splunk UI origin (interactive dashboards, deep links)."""
    return (os.getenv("SPLUNK_WEB_BASE") or "https://arlo.splunkcloud.com").rstrip("/")


def splunk_p0_default_timerange_hours() -> int:
    """
    Default lookback for P0 Streaming / CVR / ADT / US dashboards and the P0 Splunk semaphore.
    Override with env SPLUNK_P0_DEFAULT_TIMERANGE_HOURS (hours, min 4).
    """
    try:
        v = int((os.getenv("SPLUNK_P0_DEFAULT_TIMERANGE_HOURS") or "24").strip())
        return max(4, min(v, 8760))
    except ValueError:
        return 24


def splunk_p0_coerce_timerange_hours(timerange) -> int:
    """Normalize timerange from UI (int), MCP (e.g. 24h, 2d), or None → hours."""
    if timerange is None or timerange == "":
        return splunk_p0_default_timerange_hours()
    if isinstance(timerange, bool):
        return splunk_p0_default_timerange_hours()
    if isinstance(timerange, int):
        return max(1, min(timerange, 8760))
    if isinstance(timerange, float):
        return max(1, min(int(timerange), 8760))
    s = str(timerange).strip().lower()
    if s.endswith("h"):
        try:
            return max(1, int(s[:-1].strip()))
        except ValueError:
            return splunk_p0_default_timerange_hours()
    if s.endswith("d"):
        try:
            return max(1, int(s[:-1].strip()) * 24)
        except ValueError:
            return splunk_p0_default_timerange_hours()
    if s.endswith("w"):
        try:
            return max(1, int(s[:-1].strip()) * 24 * 7)
        except ValueError:
            return splunk_p0_default_timerange_hours()
    try:
        return max(1, int(float(s)))
    except ValueError:
        return splunk_p0_default_timerange_hours()


def _splunk_p0_predict_empty_panel_html(
    zmap: dict, timerange_hours: int, host_match: str, headline: str
) -> str:
    """Yellow box: distinguish REST failures vs no matching events vs parse issues."""
    failed_zones = [
        zn
        for zn in ("z1", "z2", "z3", "z4")
        if (zmap.get(zn) or {}).get("error") == "query_failed"
    ]
    raw_total = sum(
        int((zmap.get(zn) or {}).get("raw_row_count") or 0) for zn in ("z1", "z2", "z3", "z4")
    )
    idx_disp = html.escape(splunk_p0_streaming_index())
    host_note = f" (host filter: {html.escape(host_match)})" if host_match else ""
    parts = [
        f"<p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ {headline}{host_note}</p>"
    ]
    if failed_zones:
        parts.append(
            "<p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'><strong>Splunk REST errors</strong> for "
            f"zone(s) {', '.join(failed_zones)} — check server logs for HTTP status, token expiry, or IP allowlist "
            f"(port {html.escape(splunk_mgmt_port())}).</p>"
        )
        detail = None
        for zn in failed_zones:
            detail = (zmap.get(zn) or {}).get("error_detail")
            if detail:
                break
        if detail:
            parts.append(
                "<p style='margin: 8px 0 0 0; font-size: 11px; color: #92400e; word-break: break-word;'>"
                f"<strong>Last error (first zone):</strong> <code style='font-size:10px;'>{html.escape(str(detail)[:900])}</code></p>"
            )
            dl = str(detail).lower()
            if "503" in str(detail) and "concurrency" in dl:
                parts.append(
                    "<p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'>"
                    "If the message mentions <strong>role-based concurrency</strong>, Splunk was throttling parallel REST searches. "
                    "P0 zone metrics now use <strong>one combined search</strong> per request; if 503 persists, another client may be "
                    "running searches as the same Splunk user, or raise the role limit in Splunk Cloud.</p>"
                )
    elif raw_total == 0:
        parts.append(
            f"<p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'>No events matched <code>index={idx_disp}</code> in the last "
            f"<strong>{int(timerange_hours)}h</strong> for z1–z4 (<code>host</code> must match <code>…-z[1-4]-…</code> after "
            "<code>rex</code>). Try a longer time range, set <code>SPLUNK_P0_STREAMING_INDEX</code> if the index was renamed, "
            "or clear the search box if you accidentally applied a <code>host</code> filter.</p>"
        )
    else:
        parts.append(
            "<p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'>Splunk returned rows but no chart points were built "
            "(often a <code>_time</code> format change in export). Redeploy with the latest app or capture one export line for support.</p>"
        )
    inner = "".join(parts)
    return (
        "<div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; "
        f"border-radius: 4px;'>{inner}</div>"
    )


def _splunk_build_p0_predict_spl(
    zone: str,
    timerange_hours: int,
    index_literal: str = "streaming_prod",
    search_literals: str = "",
    host_match: str = "",
) -> str:
    """
    SPL for P0 zones: 15m buckets, count as upload_count.

    We intentionally omit Splunk's ``predict`` command: the REST search/jobs/export endpoint
    often runs without ML Toolkit, which yields HTTP 400 "Unknown search command 'predict'".
    Threshold bands and outlier counts are computed in Python (_splunk_rows_to_chart_series rolling band).

    search_literals: extra tokens after index, e.g. '"CVR"' for CVR dashboard.
    host_match: optional substring filter (regex-escaped) for match(host, "(?i)...")
    """
    sl = (search_literals or "").strip()
    head = f"search index={index_literal}"
    if sl:
        head = f"{head} {sl}"
    # Time window: use REST export earliest_time/latest_time only (duplicate earliest in SPL breaks some tenants).
    where = f'| where zone="{zone}"'
    hm = (host_match or "").strip()
    if hm:
        where += f' AND match(host, "(?i){re.escape(hm)}")'
    _ = int(timerange_hours)  # window applied by jobs/export earliest_time/latest_time, not duplicated in SPL
    return (
        f"{head}\n"
        f'| rex field=host "-(?<zone>z[1-4])-"\n'
        f"{where}\n"
        "| bin _time span=15m aligntime=earliest\n"
        "| stats count as upload_count by _time\n"
        "| sort 0 _time\n"
    )


def _splunk_build_p0_all_zones_spl(
    timerange_hours: int,
    index_literal: str = "streaming_prod",
    search_literals: str = "",
    host_match: str = "",
) -> str:
    """
    Single SPL for z1–z4 (one REST job). Splunk Cloud REST users (e.g. hybrid_rest_user) often hit
    HTTP 503 role concurrency limits when dispatching four parallel historical searches; one job avoids that.
    """
    sl = (search_literals or "").strip()
    head = f"search index={index_literal}"
    if sl:
        head = f"{head} {sl}"
    hm = (host_match or "").strip()
    wh_parts = ['isnotnull(zone)', 'zone IN ("z1","z2","z3","z4")']
    if hm:
        wh_parts.append(f'match(host, "(?i){re.escape(hm)}")')
    where_clause = " AND ".join(wh_parts)
    _ = int(timerange_hours)
    return (
        f"{head}\n"
        f'| rex field=host "-(?<zone>z[1-4])-"\n'
        f"| where {where_clause}\n"
        "| bin _time span=15m aligntime=earliest\n"
        "| stats count as upload_count by _time zone\n"
        "| sort 0 zone _time\n"
    )


def _splunk_rows_to_chart_series(results: list, display_tz: str) -> dict:
    """
    Parse Splunk export rows (_time + upload_count) into chart arrays + outlier count.
    Uses rolling stdev band unless Splunk returned lower/upper fields (legacy predict rows).

    For rolling bands, ``outliers`` counts points whose ``upload_count`` is strictly outside the
    same ``lower``/``upper`` arrays drawn on the chart (not a separate warmup rule).
    """
    rows = []
    for row in results or []:
        tr = row.get("_time")
        if tr is None or tr == "":
            tr = row.get("time")
        ts = _splunk_row_epoch_seconds(tr, naive_wall_timezone=display_tz)
        if ts is None:
            continue
        rows.append((ts, row))
    rows.sort(key=lambda x: x[0])

    labels = []
    ucs = []
    los = []
    his = []
    try:
        tzinfo = ZoneInfo(_splunk_resolve_p0_timezone_id(display_tz))
    except Exception:
        tzinfo = ZoneInfo("America/Los_Angeles")

    any_band = False
    outliers_predict = 0
    for ts, row in rows:
        dt = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC")).astimezone(tzinfo)
        labels.append(dt.strftime("%a, %b %d, %H:%M %Z"))
        uc = _splunk_float(row.get("upload_count"), 0.0)
        if uc is None:
            uc = 0.0
        ucs.append(uc)
        lo = hi = None
        for lk, hk in (
            ("lower", "upper"),
            ("lower95(prediction)", "upper95(prediction)"),
            ("lower95(upload_count)", "upper95(upload_count)"),
        ):
            lo = _splunk_float(row.get(lk))
            hi = _splunk_float(row.get(hk))
            if lo is not None and hi is not None:
                break
        los.append(lo)
        his.append(hi)
        if lo is not None and hi is not None:
            any_band = True
            if uc < lo or uc > hi:
                outliers_predict += 1

    if not ucs:
        return {
            "labels": [],
            "upload_count": [],
            "lower": [],
            "upper": [],
            "outliers": 0,
            "total_upload_count": 0,
            "band": "none",
        }

    if not any_band:
        # Rolling ±2σ on past buckets only (same thresholds drawn on the chart). Outlier count must
        # match what you see: count whenever upload_count is outside [lo, hi] for that bucket — do not
        # skip the first `win` indices (that caused “spike on chart but 0 outliers”).
        win = min(96, max(8, len(ucs) // 4))
        rl, ru, ob = [], [], 0
        for i, uc in enumerate(ucs):
            start = max(0, i - win)
            seg = ucs[start:i]
            if len(seg) < 3:
                seg = ucs[: max(1, i)]
            m = mean(seg)
            s = pstdev(seg) if len(seg) > 1 else 0.0
            lo = max(0.0, m - 2 * s)
            hi = m + 2 * s
            rl.append(lo)
            ru.append(hi)
            if uc < lo or uc > hi:
                ob += 1
        los, his, outliers_predict = rl, ru, ob
        band = "rolling"
    else:
        band = "predict"

    return {
        "labels": labels,
        "upload_count": ucs,
        "lower": los,
        "upper": his,
        "outliers": outliers_predict,
        "total_upload_count": int(sum(ucs)),
        "band": band,
    }


def _splunk_fetch_p0_zones_predict(
    splunk_host: str,
    splunk_token: str,
    timerange_hours: int,
    earliest_time: str,
    latest_time: str,
    search_literals: str = "",
    index_literal: str | None = None,
    max_workers: int = 4,
    host_match: str = "",
) -> dict:
    """
    Run P0 zone SPL (upload_count per 15m per zone). Band/outliers from Python.

    Uses **one** REST search for all zones (z1–z4) to avoid Splunk Cloud HTTP 503
    "role-based concurrency limit" when the REST identity runs several historical
    searches at once. ``max_workers`` is kept for API compatibility and ignored here.
    """
    _ = max_workers  # was used for parallel per-zone jobs; single search replaces that
    idx = (index_literal or "").strip() or splunk_p0_streaming_index()
    spl = _splunk_build_p0_all_zones_spl(
        timerange_hours,
        index_literal=idx,
        search_literals=search_literals,
        host_match=host_match,
    )
    tz_job = splunk_p0_job_timezone()
    _key, rows, err = execute_splunk_query(
        "p0_zones_all",
        spl,
        splunk_host,
        splunk_token,
        earliest_time,
        latest_time,
        timezone=tz_job,
    )
    display_tz = tz_job
    empty_series = {
        "labels": [],
        "upload_count": [],
        "lower": [],
        "upper": [],
        "outliers": 0,
        "total_upload_count": 0,
        "band": "none",
        "raw_row_count": 0,
    }
    if err:
        out = {}
        for zn in ("z1", "z2", "z3", "z4"):
            out[zn] = {
                **empty_series,
                "error": "query_failed",
                "error_detail": err,
            }
        return out

    by_zone: dict[str, list] = {"z1": [], "z2": [], "z3": [], "z4": []}
    for row in rows or []:
        zn = row.get("zone")
        if zn is None:
            continue
        zn = str(zn).strip()
        if zn not in by_zone:
            continue
        by_zone[zn].append(
            {"_time": row.get("_time"), "upload_count": row.get("upload_count")}
        )

    out = {}
    for zn in ("z1", "z2", "z3", "z4"):
        zrows = by_zone[zn]
        s = _splunk_rows_to_chart_series(zrows, display_tz)
        s["error"] = None
        s["raw_row_count"] = len(zrows)
        out[zn] = s
    return out


def _splunk_chartjs_p0_panel_html(
    zone_key: str,
    zone_title: str,
    chart_id: str,
    zone_color: str,
    series: dict,
) -> str:
    outliers = int(series.get("outliers") or 0)
    total_uc = int(series.get("total_upload_count") or 0)
    warn_icon = "⚠️ " if outliers > 0 else ""
    band_note = series.get("band") or ""
    sub = f"Metrics use 15m buckets · band: {band_note} (timezone {html.escape(splunk_p0_job_timezone())})"
    return f"""
            <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{warn_icon}{html.escape(zone_title)}</span>
                            <div style='font-size: 9px; color: #64748b; margin-top: 4px;'>{html.escape(sub)}</div>
                        </div>
                        <div style='text-align: right;'>
                            <div style='font-size: 20px; font-weight: bold; color: {"#dc2626" if outliers else "#059669"};'>{outliers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>outliers</div>
                        </div>
                    </div>
                    <div style='margin-top: 6px; display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 10px; color: #6b7280;'>Σ upload_count: </span>
                            <span style='font-size: 13px; font-weight: bold; color: {zone_color};'>{total_uc:,}</span>
                        </div>
                    </div>
                </div>
                <div style='position: relative; height: 180px;'>
                    <canvas id="{html.escape(chart_id)}"></canvas>
                </div>
            </div>
            """


def _splunk_chartjs_p0_script_json(chart_data: dict, canvas_prefix: str) -> str:
    """chart_data: zone_key -> series dict with labels, upload_count, lower, upper"""
    payload = {}
    for zk, ser in chart_data.items():
        payload[zk] = {
            "labels": ser.get("labels") or [],
            "upload_count": ser.get("upload_count") or [],
            "lower": ser.get("lower") or [],
            "upper": ser.get("upper") or [],
        }
    chart_json = json.dumps(payload)
    esc_prefix = json.dumps(canvas_prefix)
    return f"""
        <script>
        (function() {{
            const bundle = {chart_json};
            const prefix = {esc_prefix};
            const colors = {{
                "z1": "#4e79a7",
                "z2": "#f28e2c",
                "z3": "#e15759",
                "z4": "#76b7b2"
            }};
            const fillRgb = "54, 162, 235";
            function resizePrefixCharts() {{
                if (typeof Chart === "undefined" || !Chart.getChart) return;
                Object.keys(bundle).forEach((zone) => {{
                    const canvas = document.getElementById(prefix + zone);
                    if (!canvas) return;
                    const ch = Chart.getChart(canvas);
                    if (ch) ch.resize();
                }});
            }}
            Object.keys(bundle).forEach((zone) => {{
                const canvas = document.getElementById(prefix + zone);
                if (!canvas) return;
                if (typeof Chart !== "undefined" && Chart.getChart) {{
                    const existing = Chart.getChart(canvas);
                    if (existing) existing.destroy();
                }}
                const z = bundle[zone];
                const labels = z.labels || [];
                const up = (z.upload_count || []).map(Number);
                const lo = (z.lower || []).map((v) => (v == null ? null : Number(v)));
                const hi = (z.upper || []).map((v) => (v == null ? null : Number(v)));
                const col = colors[zone] || "#0c2461";
                const datasets = [];
                if (hi.some((x) => x != null && !isNaN(x)) && lo.some((x) => x != null && !isNaN(x))) {{
                    datasets.push({{
                        label: "upper",
                        data: hi,
                        borderColor: "rgba(" + fillRgb + ",0.35)",
                        backgroundColor: "rgba(" + fillRgb + ",0.18)",
                        pointRadius: 0,
                        fill: "+1",
                        tension: 0.2,
                    }});
                    datasets.push({{
                        label: "lower",
                        data: lo,
                        borderColor: "transparent",
                        backgroundColor: "transparent",
                        pointRadius: 0,
                        fill: false,
                        tension: 0.2,
                    }});
                }}
                datasets.push({{
                    label: "upload_count",
                    data: up,
                    borderColor: col,
                    backgroundColor: "transparent",
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.2,
                }});
                new Chart(canvas, {{
                    type: "line",
                    data: {{ labels, datasets }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {{ mode: "index", intersect: false }},
                        plugins: {{
                            legend: {{ display: false }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(item) {{
                                        const y = item.parsed.y;
                                        if (item.dataset.label === "upload_count") return "upload_count: " + (y != null ? Number(y).toLocaleString(undefined, {{maximumFractionDigits: 2}}) : "");
                                        return item.dataset.label + ": " + y;
                                    }},
                                    footer: function(items) {{
                                        if (!items || !items.length) return "";
                                        const i = items[0].dataIndex;
                                        if (lo[i] != null && hi[i] != null && !isNaN(lo[i]) && !isNaN(hi[i]))
                                            return "Thresholds: " + Number(lo[i]).toFixed(2) + " – " + Number(hi[i]).toFixed(2);
                                        return "";
                                    }},
                                }},
                            }},
                        }},
                        scales: {{
                            x: {{
                                ticks: {{ maxRotation: 45, minRotation: 0, font: {{ size: 8 }} }},
                                grid: {{ color: "rgba(0,0,0,0.06)" }},
                            }},
                            y: {{
                                beginAtZero: true,
                                ticks: {{
                                    font: {{ size: 9 }},
                                    callback: function(v) {{
                                        return v >= 1e6 ? (v/1e6).toFixed(1) + "M" : (v >= 1e3 ? (v/1e3).toFixed(1) + "k" : v);
                                    }},
                                }},
                                grid: {{ color: "rgba(0,0,0,0.06)" }},
                            }},
                        }},
                    }},
                }});
            }});
            resizePrefixCharts();
        }})();
        </script>
        """


def splunk_outliers_monitor_payload(timerange_hours=None) -> dict:
    """
    Compact JSON for home sidebar: outlier counts per zone for each Splunk P0 tool
    (same P0 zone SPL as the chat dashboards; rolling band in Python).
    """
    token = os.getenv("SPLUNK_TOKEN")
    if not token:
        return {"success": False, "error": "SPLUNK_TOKEN not configured", "tools": []}
    host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    tr = max(4, splunk_p0_coerce_timerange_hours(timerange_hours))
    earliest = f"-{tr}h@h"
    latest = "now"
    display_tz = splunk_p0_job_timezone()

    tools_cfg = [
        (
            "p0_streaming",
            "P0 Streaming",
            "",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard",
        ),
        (
            "p0_cvr",
            "P0 CVR Streaming",
            '"CVR"',
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard",
        ),
        (
            "p0_adt",
            "P0 ADT Streaming",
            "",
            "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp",
        ),
        (
            "p0_streaming_us_infra",
            "P0 Streaming US",
            "",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard__us_infra",
        ),
    ]

    tools_out = []
    for tid, label, lit, url in tools_cfg:
        zdata = _splunk_fetch_p0_zones_predict(host, token, tr, earliest, latest, search_literals=lit)
        zones = []
        tot = 0
        for zn in ("z1", "z2", "z3", "z4"):
            s = zdata.get(zn) or {}
            o = int(s.get("outliers") or 0)
            tot += o
            zones.append(
                {
                    "zone": zn,
                    "outliers": o,
                    "points": len(s.get("labels") or []),
                    "error": s.get("error"),
                }
            )
        tools_out.append(
            {
                "id": tid,
                "label": label,
                "dashboard_url": url,
                "total_outliers": tot,
                "zones": zones,
            }
        )

    return {
        "success": True,
        "error": None,
        "timerange_hours": tr,
        "timezone": display_tz,
        "tools": tools_out,
    }


def format_timestamp_range_splunk(
    from_timestamp: int, to_timestamp: int, tz_name: str | None = None
) -> str:
    """Format epoch range in the given IANA timezone (default: Splunk display TZ, else Pacific)."""
    tz_id = (tz_name or splunk_display_timezone() or "America/Los_Angeles").strip()
    try:
        zi = ZoneInfo(tz_id)
    except Exception:
        zi = ZoneInfo("America/Los_Angeles")
    from_dt = datetime.fromtimestamp(float(from_timestamp), tz=ZoneInfo("UTC")).astimezone(zi)
    to_dt = datetime.fromtimestamp(float(to_timestamp), tz=ZoneInfo("UTC")).astimezone(zi)

    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    from_day = from_dt.strftime("%A")
    to_day = to_dt.strftime("%A")

    return f"""
    <div style='display: flex; justify-content: space-around; background: rgba(255,255,255,0.1); padding: 8px; border-radius: 4px; margin-top: 8px;'>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>From ({tz_id})</div>
            <div style='font-weight: bold; font-size: 11px;'>{from_str}</div>
            <div style='font-size: 9px; opacity: 0.7;'>{from_day}</div>
        </div>
        <div style='display: flex; align-items: center; font-size: 16px;'>→</div>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>To ({tz_id})</div>
            <div style='font-weight: bold; font-size: 11px;'>{to_str}</div>
            <div style='font-size: 9px; opacity: 0.7;'>{to_day}</div>
        </div>
    </div>
    """

def generate_splunk_error_help(error_message: str) -> str:
    """Generate helpful error message with troubleshooting steps"""
    html = f"""
    <div style='background: #fef2f2; border-left: 4px solid #dc2626; padding: 20px; margin: 20px 0; border-radius: 6px;'>
        <h3 style='margin: 0 0 10px 0; color: #991b1b;'>⚠️ Splunk Connection Failed</h3>
        <p style='margin: 10px 0; color: #7f1d1d;'><strong>Error:</strong> {error_message}</p>
        
        <h4 style='margin: 15px 0 10px 0; color: #991b1b;'>🔍 Most Common Causes:</h4>
        <ol style='margin: 10px 0; padding-left: 25px; color: #7f1d1d;'>
            <li><strong>IP Not Whitelisted:</strong> Your IP address needs to be added to Splunk Cloud's IP allowlist</li>
            <li><strong>VPN Required:</strong> You may need to connect to your corporate VPN</li>
            <li><strong>Port 8089 Blocked:</strong> Firewall may be blocking the required port</li>
            <li><strong>Invalid Token:</strong> SPLUNK_TOKEN may be expired or incorrect</li>
        </ol>
        
        <h4 style='margin: 15px 0 10px 0; color: #991b1b;'>✅ Troubleshooting Steps:</h4>
        <ol style='margin: 10px 0; padding-left: 25px; color: #7f1d1d;'>
            <li><strong>Check your IP:</strong> Visit <a href='https://whatismyipaddress.com' target='_blank' style='color: #dc2626;'>whatismyipaddress.com</a></li>
            <li><strong>Contact Splunk Admin:</strong> Request to whitelist your IP or CIDR range</li>
            <li><strong>Connect to VPN:</strong> If required by your organization</li>
            <li><strong>Verify Token:</strong> Check that SPLUNK_TOKEN in .env is valid</li>
            <li><strong>Test Connectivity:</strong> Try accessing <a href='https://arlo.splunkcloud.com' target='_blank' style='color: #dc2626;'>arlo.splunkcloud.com</a></li>
        </ol>
        
        <div style='background: #fef3c7; padding: 12px; margin-top: 15px; border-radius: 4px;'>
            <p style='margin: 0; color: #78350f; font-size: 13px;'>
                💡 <strong>Quick Fix:</strong> Most Splunk Cloud instances require IP whitelisting. 
                Contact your Splunk administrator to add your IP address to the allowlist.
            </p>
        </div>
    </div>
    """
    return html

def read_splunk_p0_dashboard(query: str = "", timerange=None, *, us_infra: bool = False) -> str:
    """
    Shows the P0 Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Hours lookback (int or MCP string like 24h); default 24h.
        us_infra: If True, embed the Splunk US infra dashboard deep link and US-specific titles (same REST/charts).
    """
    timerange_hours = splunk_p0_coerce_timerange_hours(timerange)
    print("=" * 80)
    print("📊 Reading Splunk P0 US Infra Dashboard" if us_infra else "📊 Reading Splunk P0 Dashboard")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Splunk credentials from environment
    splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    splunk_token = os.getenv("SPLUNK_TOKEN")
    
    if not splunk_token:
        return """
        <p>❌ Splunk credentials not configured. Please set <strong>SPLUNK_TOKEN</strong> in your .env file.</p>
        """
    
    # Get public IP for whitelist verification
    try:
        public_ip_response = requests.get("https://api.ipify.org", timeout=15)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    _sw = splunk_web_base_url()
    dash_slug = "p0_streaming_dashboard__us_infra" if us_infra else "p0_streaming_dashboard"
    dash_title = "Splunk - P0 Streaming US" if us_infra else "Splunk - P0 Streaming Dashboard"
    dash_subtitle = (
        "P0 Streaming US infra — zones z1–z4 (same predict pipeline as P0 Streaming)"
        if us_infra
        else "Real-time monitoring of P0 streaming services"
    )
    # Deep link aligned with the same lookback as this REST view (hours).
    dashboard_url = (
        f"{_sw}/en-US/app/arlo_sre/{dash_slug}"
        f"?form.tok_time.earliest=-{timerange_hours}h&form.tok_time.latest=now"
    )

    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time, splunk_p0_job_timezone())
    
    # Dashboard header
    output += f"""
    <div style='background: linear-gradient(135deg, #00c853 0%, #00796b 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 0 0 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 {html.escape(dash_title)}</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            {html.escape(dash_subtitle)}
        </p>
        <p style='margin: 0 0 8px 0;'>
            <a href='{dashboard_url}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                Open Interactive Dashboard →
            </a>
        </p>
        {timestamp_range_html}
    </div>
    """
    
    if query:
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
            <p style='margin: 0; font-size: 12px; color: #856404;'>
                🔍 <strong>Filtering for:</strong> {html.escape(query)}
            </p>
        </div>
        """
    
    try:
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"

        zone_colors = {
            "z1": "#4e79a7",
            "z2": "#f28e2c",
            "z3": "#e15759",
            "z4": "#76b7b2",
        }

        output += f"""
        <div style='margin: 8px 0; padding: 8px 10px; background: #eff6ff; border-left: 4px solid #2563eb; border-radius: 6px; font-size: 11px; color: #1e3a8a; line-height: 1.45;'>
            <strong>Splunk REST (this view):</strong> timezone <code>{html.escape(splunk_p0_job_timezone())}</code> (US Pacific PST/PDT — buckets + chart aligned with Splunk UI);
            <strong>15m</strong> buckets; <code>upload_count</code> = event count per bucket.
            <strong>Band / outliers:</strong> computed here with a rolling ±2σ window on <code>upload_count</code> (REST cannot rely on Splunk’s <code>predict</code>/MLTK on many tenants — avoid HTTP 400).
            The interactive Splunk dashboard may still use <code>predict</code> LLP when ML Toolkit is available there.
            <br><br>
            <strong>Time range:</strong> OneView defaults to <strong>{int(splunk_p0_default_timerange_hours())}h</strong> for P0 tools; the <a href="{html.escape(dashboard_url)}" target="_blank" rel="noopener noreferrer" style="color: #1d4ed8;">Splunk dashboard</a> link uses the <strong>same</strong> lookback as this page (<code>earliest=-{int(timerange_hours)}h</code>). Widen in either UI if needed.
        </div>
        """

        host_match = (query or "").strip()
        zmap = _splunk_fetch_p0_zones_predict(
            splunk_host,
            splunk_token,
            timerange_hours,
            earliest_time,
            latest_time,
            search_literals="",
            host_match=host_match,
        )

        nonempty = any(
            len((zmap.get(zn) or {}).get("labels") or []) > 0 for zn in ("z1", "z2", "z3", "z4")
        )
        if not nonempty:
            output += _splunk_p0_predict_empty_panel_html(
                zmap,
                timerange_hours,
                host_match,
                "No streaming recording data for this query",
            )
            return output

        chart_bundle = {}
        output += """
        <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0;'>
        """
        for zone_num in ["1", "2", "3", "4"]:
            zone_key = f"z{zone_num}"
            ser = zmap.get(zone_key) or {}
            chart_id = f"chart_p0_{zone_key}"
            output += _splunk_chartjs_p0_panel_html(
                zone_key,
                f"Zone {zone_num} (Recording Uploads)",
                chart_id,
                zone_colors.get(zone_key, "#4e79a7"),
                ser,
            )
            chart_bundle[zone_key] = ser
        output += "</div>"
        output += _splunk_chartjs_p0_script_json(chart_bundle, "chart_p0_")

        all_queries = {}
        _p0_idx = splunk_p0_streaming_index()
        all_queries["servers"] = f'''| tstats dc(host) as server_count where index={_p0_idx} by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        all_queries["jvm"] = f'''| search index={_p0_idx} ("JVM" OR "OutOfMemoryError" OR "crash")
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0'''
        all_results = execute_splunk_queries_parallel(
            all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=2
        )

        # ========== ADDITIONAL METRICS ==========
        
        # 2. Active Servers by Zone
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📡 Active Servers</h3>"
        
        servers_data = all_results.get('servers') or []
        if len(servers_data) > 0:
            if len(servers_data) > 0:
                timestamps_servers = []
                zone_servers = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in servers_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_servers.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_servers.append("")
                    except:
                        timestamps_servers.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_servers[zone].append(count)
                
                zone_server_totals = {zone: sum(counts) for zone, counts in zone_servers.items()}
                
                output += "<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0;'>"
                
                for zone_num in ["1", "2", "3", "4"]:
                    zone_key = f"z{zone_num}"
                    total_servers = zone_server_totals.get(zone_key, 0)
                    zone_color = zone_colors.get(zone_key, "#4e79a7")
                    chart_id_servers = f"chart_p0_servers_{zone_key}"
                    
                    output += f"""
                    <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        <div style='margin-bottom: 10px; text-align: center;'>
                            <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>Zone {zone_num}</span>
                            <div style='font-size: 20px; font-weight: bold; color: {zone_color}; margin-top: 4px;'>{total_servers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>servers</div>
                        </div>
                        <div style='position: relative; height: 100px;'>
                            <canvas id="{chart_id_servers}"></canvas>
                        </div>
                    </div>
                    """
                
                output += "</div>"
                
                # Chart.js script for Active Servers
                servers_data_json = json.dumps({
                    "timestamps": timestamps_servers,
                    "zones": zone_servers
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {servers_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    Object.keys(data.zones).forEach((zone) => {{
                        const chartId = `chart_p0_servers_${{zone}}`;
                        const canvas = document.getElementById(chartId);
                        
                        if (canvas) {{
                            new Chart(canvas, {{
                                type: 'line',
                                data: {{
                                    labels: data.timestamps,
                                    datasets: [{{
                                        data: data.zones[zone],
                                        borderColor: colors[zone],
                                        backgroundColor: colors[zone] + '20',
                                        fill: true,
                                        tension: 0.4,
                                        borderWidth: 2,
                                        pointRadius: 2
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ legend: {{ display: false }} }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ display: true, beginAtZero: true, ticks: {{ font: {{ size: 8 }} }} }}
                                    }}
                                }}
                            }});
                        }}
                    }});
                }})();
                </script>
                """
        
        # 3. JVM Crashes
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔥 JVM Crash - Error Count</h3>"
        
        jvm_data = all_results.get('jvm') or []
        if len(jvm_data) > 0:
            if len(jvm_data) > 0:
                timestamps_jvm = []
                zone_jvm = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in jvm_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_jvm.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_jvm.append("")
                    except:
                        timestamps_jvm.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_jvm[zone].append(count)
                
                zone_jvm_totals = {zone: sum(counts) for zone, counts in zone_jvm.items()}
                total_jvm_errors = sum(zone_jvm_totals.values())
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>Total JVM Errors: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #dc2626;'>{total_jvm_errors}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_p0_jvm"></canvas>
                    </div>
                </div>
                """
                
                jvm_data_json = json.dumps({
                    "timestamps": timestamps_jvm,
                    "zones": zone_jvm
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {jvm_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    const datasets = [];
                    Object.keys(data.zones).forEach((zone) => {{
                        datasets.push({{
                            label: 'Zone ' + zone.replace('z', ''),
                            data: data.zones[zone],
                            borderColor: colors[zone],
                            backgroundColor: colors[zone] + '80',
                            borderWidth: 2
                        }});
                    }});
                    
                    const canvas = document.getElementById('chart_p0_jvm');
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'bar',
                            data: {{
                                labels: data.timestamps,
                                datasets: datasets
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: true, position: 'top' }}
                                }},
                                scales: {{
                                    x: {{ stacked: false }},
                                    y: {{ stacked: false, beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>✅ No JVM errors found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a connection error
        error_str = str(e).lower()
        if 'timeout' in error_str or 'connection' in error_str or 'max retries' in error_str:
            return generate_splunk_error_help(str(e))
        
        return f"<p>❌ Error reading Splunk dashboard: {html.escape(str(e))}</p>"


def read_splunk_p0_cvr_dashboard(query: str = "", timerange=None) -> str:
    """
    Shows the P0 CVR Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Hours lookback; default 24h (see splunk_p0_default_timerange_hours).
    """
    timerange_hours = splunk_p0_coerce_timerange_hours(timerange)
    print("=" * 80)
    print("📊 Reading Splunk P0 CVR Dashboard")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Splunk credentials from environment
    splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    splunk_token = os.getenv("SPLUNK_TOKEN")
    
    if not splunk_token:
        return """
        <p>❌ Splunk credentials not configured. Please set <strong>SPLUNK_TOKEN</strong> in your .env file.</p>
        """
    
    # Get public IP for whitelist verification
    try:
        public_ip_response = requests.get("https://api.ipify.org", timeout=15)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time, splunk_p0_job_timezone())
    
    # Dashboard header with different color scheme
    output += f"""
    <div style='background: linear-gradient(135deg, #9c27b0 0%, #6a1b9a 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 0 0 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 CVR Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 CVR streaming services
        </p>
        <p style='margin: 0 0 8px 0;'>
            <a href='{dashboard_url}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                Open Interactive Dashboard →
            </a>
        </p>
        {timestamp_range_html}
    </div>
    """
    
    if query:
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
            <p style='margin: 0; font-size: 12px; color: #856404;'>
                🔍 <strong>Filtering for:</strong> {html.escape(query)}
            </p>
        </div>
        """
    
    try:
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"
        zone_colors = {
            "z1": "#4e79a7",
            "z2": "#f28e2c",
            "z3": "#e15759",
            "z4": "#76b7b2",
        }
        output += f"""
        <div style='margin: 8px 0; padding: 8px 10px; background: #f3e8ff; border-left: 4px solid #7c3aed; border-radius: 6px; font-size: 11px; color: #4c1d95; line-height: 1.45;'>
            Same logic as P0 Streaming: 15m buckets, <code>upload_count</code>, rolling band / outliers in OneView, TZ <code>{html.escape(splunk_p0_job_timezone())}</code>.
            Search scoped with term <code>CVR</code> in the index.
        </div>
        """
        host_match = (query or "").strip()
        zmap = _splunk_fetch_p0_zones_predict(
            splunk_host,
            splunk_token,
            timerange_hours,
            earliest_time,
            latest_time,
            search_literals='"CVR"',
            host_match=host_match,
        )
        nonempty = any(
            len((zmap.get(zn) or {}).get("labels") or []) > 0 for zn in ("z1", "z2", "z3", "z4")
        )
        if not nonempty:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No CVR recording series for this query{f" (host filter: {html.escape(host_match)})" if host_match else ""}</p>
            </div>
            """
            return output
        chart_bundle = {}
        output += """
        <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0;'>
        """
        for zone_num in ["1", "2", "3", "4"]:
            zone_key = f"z{zone_num}"
            ser = zmap.get(zone_key) or {}
            chart_id = f"chart_cvr_{zone_key}"
            output += _splunk_chartjs_p0_panel_html(
                zone_key,
                f"Zone {zone_num} (CVR Uploads)",
                chart_id,
                zone_colors.get(zone_key, "#4e79a7"),
                ser,
            )
            chart_bundle[zone_key] = ser
        output += "</div>"
        output += _splunk_chartjs_p0_script_json(chart_bundle, "chart_cvr_")

        all_queries = {}
        _p0_idx = splunk_p0_streaming_index()
        all_queries["devices"] = f'''| tstats dc(device_id) as device_count where index={_p0_idx} "CVR" by _time span=1h
| timechart span=1h sum(device_count) as devices
| fillnull value=0'''
        all_queries["connections"] = f'''| search index={_p0_idx} "CVR" "connection"
| timechart span=1h count as connections
| fillnull value=0'''
        all_results = execute_splunk_queries_parallel(
            all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=2
        )

        # ========== ADDITIONAL CVR METRICS ==========
        
        # CVR Active Devices
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📱 CVR Active Devices</h3>"
        
        devices_data = all_results.get('devices') or []
        if len(devices_data) > 0:
            if len(devices_data) > 0:
                timestamps_devices = []
                device_counts = []
                
                for datapoint in devices_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_devices.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_devices.append("")
                    except:
                        timestamps_devices.append(str(timestamp_raw))
                    
                    device_counts.append(int(datapoint.get("devices", 0)))
                
                total_devices = sum(device_counts)
                avg_devices = total_devices // len(device_counts) if device_counts else 0
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; color: #2d3748;'>Total Active Devices: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #9c27b0;'>{total_devices:,}</span>
                        <span style='font-size: 12px; color: #6b7280; margin-left: 12px;'>Avg: {avg_devices:,}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_cvr_devices"></canvas>
                    </div>
                </div>
                """
                
                devices_data_json = json.dumps({
                    "timestamps": timestamps_devices,
                    "devices": device_counts
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {devices_data_json};
                    const canvas = document.getElementById('chart_cvr_devices');
                    
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'line',
                            data: {{
                                labels: data.timestamps,
                                datasets: [{{
                                    label: 'Active Devices',
                                    data: data.devices,
                                    borderColor: '#9c27b0',
                                    backgroundColor: '#9c27b020',
                                    fill: true,
                                    tension: 0.4,
                                    borderWidth: 2,
                                    pointRadius: 3
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: false }}
                                }},
                                scales: {{
                                    x: {{
                                        ticks: {{
                                            maxRotation: 45,
                                            minRotation: 45,
                                            font: {{ size: 9 }}
                                        }}
                                    }},
                                    y: {{
                                        beginAtZero: true,
                                        ticks: {{
                                            callback: function(value) {{
                                                return value >= 1000 ? (value/1000).toFixed(1) + 'k' : value;
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>No device data found</p>"
        
        # CVR Connections Count
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔌 CVR Connections Count</h3>"
        
        connections_data = all_results.get('connections') or []
        if len(connections_data) > 0:
            if len(connections_data) > 0:
                connection_counts = []
                
                for datapoint in connections_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_conn.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_conn.append("")
                    except:
                        timestamps_conn.append(str(timestamp_raw))
                    
                    connection_counts.append(int(datapoint.get("connections", 0)))
                
                total_connections = sum(connection_counts)
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; color: #2d3748;'>Total Connections: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #ff9800;'>{total_connections:,}</span>
                    </div>
                    <div style='position: relative; height: 180px;'>
                        <canvas id="chart_cvr_connections"></canvas>
                    </div>
                </div>
                """
                
                connections_data_json = json.dumps({
                    "timestamps": timestamps_conn,
                    "connections": connection_counts
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {connections_data_json};
                    const canvas = document.getElementById('chart_cvr_connections');
                    
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'line',
                            data: {{
                                labels: data.timestamps,
                                datasets: [{{
                                    label: 'Connections',
                                    data: data.connections,
                                    borderColor: '#ff9800',
                                    backgroundColor: '#ff980020',
                                    fill: true,
                                    tension: 0.4,
                                    borderWidth: 2,
                                    pointRadius: 2
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{ legend: {{ display: false }} }},
                                scales: {{
                                    x: {{
                                        ticks: {{
                                            maxRotation: 45,
                                            minRotation: 45,
                                            font: {{ size: 9 }}
                                        }}
                                    }},
                                    y: {{ beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>No connection data found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk CVR dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a connection error
        error_str = str(e).lower()
        if 'timeout' in error_str or 'connection' in error_str or 'max retries' in error_str:
            return generate_splunk_error_help(str(e))
        
        return f"<p>❌ Error reading Splunk CVR dashboard: {html.escape(str(e))}</p>"


def read_splunk_p0_adt_dashboard(query: str = "", timerange=None) -> str:
    """
    Shows the P0 ADT Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Hours lookback; default 24h.
    """
    timerange_hours = splunk_p0_coerce_timerange_hours(timerange)
    print("=" * 80)
    print("📊 Reading Splunk P0 ADT Dashboard")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Splunk credentials from environment
    splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    splunk_token = os.getenv("SPLUNK_TOKEN")
    
    if not splunk_token:
        return """
        <p>❌ Splunk credentials not configured. Please set <strong>SPLUNK_TOKEN</strong> in your .env file.</p>
        """
    
    # Get public IP for whitelist verification
    try:
        public_ip_response = requests.get("https://api.ipify.org", timeout=15)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time, splunk_p0_job_timezone())
    
    # Dashboard header with orange/red theme
    output += f"""
    <div style='background: linear-gradient(135deg, #ff6f00 0%, #e65100 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 0 0 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 ADT Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 ADT streaming services
        </p>
        <p style='margin: 0 0 8px 0;'>
            <a href='{dashboard_url}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                Open Interactive Dashboard →
            </a>
        </p>
        {timestamp_range_html}
    </div>
    """
    
    if query:
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
            <p style='margin: 0; font-size: 12px; color: #856404;'>
                🔍 <strong>Filtering for:</strong> {html.escape(query)}
            </p>
        </div>
        """
    
    try:
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"
        zone_colors = {
            "z1": "#4e79a7",
            "z2": "#f28e2c",
            "z3": "#e15759",
            "z4": "#76b7b2",
        }
        output += f"""
        <div style='margin: 8px 0; padding: 8px 10px; background: #fff7ed; border-left: 4px solid #ea580c; border-radius: 6px; font-size: 11px; color: #7c2d12; line-height: 1.45;'>
            Same band / outlier logic as P0 Streaming (no CVR term). TZ <code>{html.escape(splunk_p0_job_timezone())}</code>.
        </div>
        """
        host_match = (query or "").strip()
        zmap = _splunk_fetch_p0_zones_predict(
            splunk_host,
            splunk_token,
            timerange_hours,
            earliest_time,
            latest_time,
            search_literals="",
            host_match=host_match,
        )
        nonempty = any(
            len((zmap.get(zn) or {}).get("labels") or []) > 0 for zn in ("z1", "z2", "z3", "z4")
        )
        if not nonempty:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No ADT recording series for this query{f" (host filter: {html.escape(host_match)})" if host_match else ""}</p>
            </div>
            """
            return output
        chart_bundle = {}
        output += """
        <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0;'>
        """
        for zone_num in ["1", "2", "3", "4"]:
            zone_key = f"z{zone_num}"
            ser = zmap.get(zone_key) or {}
            chart_id = f"chart_adt_{zone_key}"
            output += _splunk_chartjs_p0_panel_html(
                zone_key,
                f"Zone {zone_num} (ADT Uploads)",
                chart_id,
                zone_colors.get(zone_key, "#4e79a7"),
                ser,
            )
            chart_bundle[zone_key] = ser
        output += "</div>"
        output += _splunk_chartjs_p0_script_json(chart_bundle, "chart_adt_")

        all_queries = {}
        _p0_idx = splunk_p0_streaming_index()
        all_queries["servers"] = f'''| tstats dc(host) as server_count where index={_p0_idx} by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        all_queries["jvm"] = f'''| search index={_p0_idx} ("JVM" OR "OutOfMemoryError" OR "crash")
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0'''
        all_results = execute_splunk_queries_parallel(
            all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=2
        )

        # ========== ADDITIONAL ADT METRICS ==========
        
        # Active Servers by Zone
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📡 Active Servers</h3>"
        
        servers_data = all_results.get('servers') or []
        if len(servers_data) > 0:
            if len(servers_data) > 0:
                timestamps_servers = []
                zone_servers = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in servers_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_servers.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_servers.append("")
                    except:
                        timestamps_servers.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_servers[zone].append(count)
                
                zone_server_totals = {zone: sum(counts) for zone, counts in zone_servers.items()}
                
                output += "<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0;'>"
                
                zone_colors = {
                    "z1": "#4e79a7",
                    "z2": "#f28e2c",
                    "z3": "#e15759",
                    "z4": "#76b7b2"
                }
                
                for zone_num in ["1", "2", "3", "4"]:
                    zone_key = f"z{zone_num}"
                    total_servers = zone_server_totals.get(zone_key, 0)
                    zone_color = zone_colors.get(zone_key, "#4e79a7")
                    chart_id_servers = f"chart_adt_servers_{zone_key}"
                    
                    output += f"""
                    <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        <div style='margin-bottom: 10px; text-align: center;'>
                            <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>Zone {zone_num}</span>
                            <div style='font-size: 20px; font-weight: bold; color: {zone_color}; margin-top: 4px;'>{total_servers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>servers</div>
                        </div>
                        <div style='position: relative; height: 100px;'>
                            <canvas id="{chart_id_servers}"></canvas>
                        </div>
                    </div>
                    """
                
                output += "</div>"
                
                servers_data_json = json.dumps({
                    "timestamps": timestamps_servers,
                    "zones": zone_servers
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {servers_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    Object.keys(data.zones).forEach((zone) => {{
                        const chartId = `chart_adt_servers_${{zone}}`;
                        const canvas = document.getElementById(chartId);
                        
                        if (canvas) {{
                            new Chart(canvas, {{
                                type: 'line',
                                data: {{
                                    labels: data.timestamps,
                                    datasets: [{{
                                        data: data.zones[zone],
                                        borderColor: colors[zone],
                                        backgroundColor: colors[zone] + '20',
                                        fill: true,
                                        tension: 0.4,
                                        borderWidth: 2,
                                        pointRadius: 2
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ legend: {{ display: false }} }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ display: true, beginAtZero: true, ticks: {{ font: {{ size: 8 }} }} }}
                                    }}
                                }}
                            }});
                        }}
                    }});
                }})();
                </script>
                """
        
        # JVM Crashes
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔥 JVM Crash - Error Count</h3>"
        
        jvm_data = all_results.get('jvm') or []
        if len(jvm_data) > 0:
            if len(jvm_data) > 0:
                timestamps_jvm = []
                zone_jvm = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in jvm_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_jvm.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_jvm.append("")
                    except:
                        timestamps_jvm.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_jvm[zone].append(count)
                
                zone_jvm_totals = {zone: sum(counts) for zone, counts in zone_jvm.items()}
                total_jvm_errors = sum(zone_jvm_totals.values())
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>Total JVM Errors: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #dc2626;'>{total_jvm_errors}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_adt_jvm"></canvas>
                    </div>
                </div>
                """
                
                jvm_data_json = json.dumps({
                    "timestamps": timestamps_jvm,
                    "zones": zone_jvm
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {jvm_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    const datasets = [];
                    Object.keys(data.zones).forEach((zone) => {{
                        datasets.push({{
                            label: 'Zone ' + zone.replace('z', ''),
                            data: data.zones[zone],
                            borderColor: colors[zone],
                            backgroundColor: colors[zone] + '80',
                            borderWidth: 2
                        }});
                    }});
                    
                    const canvas = document.getElementById('chart_adt_jvm');
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'bar',
                            data: {{
                                labels: data.timestamps,
                                datasets: datasets
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: true, position: 'top' }}
                                }},
                                scales: {{
                                    x: {{ stacked: false }},
                                    y: {{ stacked: false, beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>✅ No JVM errors found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk ADT dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a connection error
        error_str = str(e).lower()
        if 'timeout' in error_str or 'connection' in error_str or 'max retries' in error_str:
            return generate_splunk_error_help(str(e))
        
        return f"<p>❌ Error reading Splunk ADT dashboard: {html.escape(str(e))}</p>"


def read_splunk_p0_us_infra_dashboard(query: str = "", timerange=None) -> str:
    """
    P0 Streaming US infra dashboard in Splunk — same predict / z1–z4 zone logic as P0 Streaming;
    opens the US infra dashboard view.
    """
    return read_splunk_p0_dashboard(query, timerange, us_infra=True)
