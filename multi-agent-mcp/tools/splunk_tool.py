import os
import re
import html
import requests
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import mean, pstdev
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()


def splunk_search_timezone() -> str:
    """IANA zone for REST jobs (earliest/latest + predict). Default PST."""
    return (os.getenv("SPLUNK_SEARCH_TIMEZONE") or "America/Los_Angeles").strip()


def splunk_display_timezone() -> str:
    """Labels / UI (same default as search unless overridden)."""
    return (os.getenv("SPLUNK_DISPLAY_TIMEZONE") or splunk_search_timezone()).strip()


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
    try:
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        tz = timezone if timezone is not None else splunk_search_timezone()
        data = {
            "search": query_data,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "output_mode": "json",
        }
        if tz:
            data["timezone"] = tz
        
        # Increased connect timeout to 30 seconds for Splunk Cloud
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(30, 180))
        if response.status_code == 400 and tz:
            data.pop("timezone", None)
            response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(30, 180))
        
        if response.status_code == 200:
            # Parse JSON results from export endpoint
            results = []
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            results.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            return query_key, results, None
        else:
            return query_key, None, f"HTTP {response.status_code}: {response.text[:200]}"
    
    except requests.exceptions.ConnectTimeout as e:
        return query_key, None, f"⚠️ Connection timeout - Your IP may not be whitelisted in Splunk Cloud: {str(e)}"
    except requests.exceptions.Timeout as e:
        return query_key, None, f"⏱️ Request timeout - Splunk query took too long: {str(e)}"
    except requests.exceptions.ConnectionError as e:
        return query_key, None, f"🔌 Connection error - Check if port 8089 is accessible or if VPN is required: {str(e)}"
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
                else:
                    results[key] = data
                    print(f"✅ Query '{key}' completed: {len(data) if data else 0} results")
            except Exception as e:
                print(f"❌ Query '{query_key}' exception: {str(e)}")
                results[query_key] = None
    
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


def _splunk_build_p0_predict_spl(
    zone: str,
    timerange_hours: int,
    index_literal: str = "streaming_prod",
    search_literals: str = "",
    host_match: str = "",
) -> str:
    """
    SPL aligned with Splunk P0 recording panels: 15m buckets, count as upload_count, predict LLP band.
    search_literals: extra tokens after index, e.g. '"CVR"' for CVR dashboard.
    host_match: optional substring filter (regex-escaped) for match(host, "(?i)...")
    """
    sl = (search_literals or "").strip()
    head = f"search index={index_literal}"
    if sl:
        head = f"{head} {sl}"
    head = f"{head} earliest=-{timerange_hours}h@h latest=now"
    where = f'| where zone="{zone}"'
    hm = (host_match or "").strip()
    if hm:
        where += f' AND match(host, "(?i){re.escape(hm)}")'
    return (
        f"{head}\n"
        f'| rex field=host "-(?<zone>z[1-4])-"\n'
        f"{where}\n"
        "| bin _time span=15m aligntime=earliest\n"
        "| stats count as upload_count by _time\n"
        "| sort 0 _time\n"
        "| predict upload_count as prediction lower95=lower upper95=upper algorithm=LLP holdback=0 future_timespan=0\n"
    )


def _splunk_rows_to_chart_series(results: list, display_tz: str) -> dict:
    """
    Parse predict export rows into chart arrays + outlier count.
    Falls back to rolling stdev band if Splunk returns no lower/upper.
    """
    rows = []
    for row in results or []:
        tr = row.get("_time")
        if tr is None or tr == "":
            continue
        try:
            ts = float(tr)
        except (TypeError, ValueError):
            continue
        rows.append((ts, row))
    rows.sort(key=lambda x: x[0])

    labels = []
    ucs = []
    los = []
    his = []
    try:
        tzinfo = ZoneInfo(display_tz)
    except Exception:
        tzinfo = ZoneInfo("America/Los_Angeles")

    any_band = False
    outliers_predict = 0
    for ts, row in rows:
        dt = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC")).astimezone(tzinfo)
        labels.append(dt.strftime("%a, %b %d, %H:%M"))
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
            if i >= win and (uc < lo or uc > hi):
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
    index_literal: str = "streaming_prod",
    max_workers: int = 4,
    host_match: str = "",
) -> dict:
    """Run predict SPL per zone (z1–z4) in parallel. Returns { 'z1': series_dict, ... }."""
    queries = {}
    for zn in ("z1", "z2", "z3", "z4"):
        queries[f"zone_{zn}"] = _splunk_build_p0_predict_spl(
            zn,
            timerange_hours,
            index_literal=index_literal,
            search_literals=search_literals,
            host_match=host_match,
        )
    raw = execute_splunk_queries_parallel(
        queries,
        splunk_host,
        splunk_token,
        earliest_time,
        latest_time,
        max_workers=max_workers,
    )
    display_tz = splunk_display_timezone()
    out = {}
    for zn in ("z1", "z2", "z3", "z4"):
        key = f"zone_{zn}"
        rows = raw.get(key)
        if rows is None:
            out[zn] = {"error": "query_failed", "labels": [], "upload_count": [], "lower": [], "upper": [], "outliers": 0, "total_upload_count": 0, "band": "none"}
        else:
            s = _splunk_rows_to_chart_series(rows, display_tz)
            s["error"] = None
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
    sub = f"Metrics use 15m buckets · band: {band_note} (timezone {html.escape(splunk_display_timezone())})"
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
    return f"""
        <script>
        (function() {{
            const bundle = {chart_json};
            const colors = {{
                "z1": "#4e79a7",
                "z2": "#f28e2c",
                "z3": "#e15759",
                "z4": "#76b7b2"
            }};
            const fillRgb = "54, 162, 235";
            Object.keys(bundle).forEach((zone) => {{
                const canvas = document.getElementById("{canvas_prefix}" + zone);
                if (!canvas) return;
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
        }})();
        </script>
        """


def splunk_outliers_monitor_payload(timerange_hours: int = 72) -> dict:
    """
    Compact JSON for home sidebar: outlier counts per zone for each Splunk P0 tool
    (same SPL/predict logic as the chat dashboards).
    """
    token = os.getenv("SPLUNK_TOKEN")
    if not token:
        return {"success": False, "error": "SPLUNK_TOKEN not configured", "tools": []}
    host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    tr = max(4, int(timerange_hours))
    earliest = f"-{tr}h@h"
    latest = "now"
    display_tz = splunk_display_timezone()

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


def format_timestamp_range_splunk(from_timestamp: int, to_timestamp: int) -> str:
    """Format timestamp range into readable format with date and time"""
    from datetime import datetime
    
    # Convert to datetime objects
    from_dt = datetime.fromtimestamp(from_timestamp)
    to_dt = datetime.fromtimestamp(to_timestamp)
    
    # Format with date and time
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    # Also include day of week for context
    from_day = from_dt.strftime("%A")
    to_day = to_dt.strftime("%A")
    
    return f"""
    <div style='display: flex; justify-content: space-around; background: rgba(255,255,255,0.1); padding: 8px; border-radius: 4px; margin-top: 8px;'>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>From</div>
            <div style='font-weight: bold; font-size: 11px;'>{from_str}</div>
            <div style='font-size: 9px; opacity: 0.7;'>{from_day}</div>
        </div>
        <div style='display: flex; align-items: center; font-size: 16px;'>→</div>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>To</div>
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

def read_splunk_p0_dashboard(query: str = "", timerange: int = 4) -> str:
    """
    Shows the P0 Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Number of hours to look back (default: 4)
    """
    timerange_hours = timerange  # Normalize parameter name
    print("=" * 80)
    print("📊 Reading Splunk P0 Dashboard")
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
        public_ip_response = requests.get("https://api.ipify.org", timeout=5)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
    # Dashboard header
    output += f"""
    <div style='background: linear-gradient(135deg, #00c853 0%, #00796b 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 0 0 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 streaming services
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
            <strong>Matches Splunk UI:</strong> timezone <code>{html.escape(splunk_display_timezone())}</code> on REST searches and chart labels;
            <strong>15m</strong> buckets; <code>upload_count</code> = event count per bucket;
            band = <code>predict … lower95/upper95</code> (LLP). <strong>Outliers</strong> = points outside that band (same criterion as the native panel when it uses the same predict).
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
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No streaming recording data for predict pipeline{f" (host filter: {html.escape(host_match)})" if host_match else ""}</p>
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
        all_queries["servers"] = f'''| tstats dc(host) as server_count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        all_queries["jvm"] = f'''| search index=streaming_prod earliest=-{timerange_hours}h ("JVM" OR "OutOfMemoryError" OR "crash")
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


def read_splunk_p0_cvr_dashboard(query: str = "", timerange: int = 4) -> str:
    """
    Shows the P0 CVR Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Number of hours to look back (default: 4)
    """
    timerange_hours = timerange  # Normalize parameter name
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
        public_ip_response = requests.get("https://api.ipify.org", timeout=5)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
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
            Same logic as P0 Streaming: 15m buckets, <code>upload_count</code>, <code>predict</code> LLP band, outliers outside the band, TZ <code>{html.escape(splunk_display_timezone())}</code>.
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
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No CVR recording series for predict{f" (host filter: {html.escape(host_match)})" if host_match else ""}</p>
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
        all_queries["devices"] = f'''| tstats dc(device_id) as device_count where index=streaming_prod earliest=-{timerange_hours}h "CVR" by _time span=1h
| timechart span=1h sum(device_count) as devices
| fillnull value=0'''
        all_queries["connections"] = f'''| search index=streaming_prod earliest=-{timerange_hours}h "CVR" "connection"
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


def read_splunk_p0_adt_dashboard(query: str = "", timerange: int = 4) -> str:
    """
    Shows the P0 ADT Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange: Number of hours to look back (default: 4)
    """
    timerange_hours = timerange  # Normalize parameter name
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
        public_ip_response = requests.get("https://api.ipify.org", timeout=5)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
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
            Same predict / outlier logic as P0 Streaming (no CVR term). TZ <code>{html.escape(splunk_display_timezone())}</code>.
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
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No ADT recording series for predict{f" (host filter: {html.escape(host_match)})" if host_match else ""}</p>
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
        all_queries["servers"] = f'''| tstats dc(host) as server_count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        all_queries["jvm"] = f'''| search index=streaming_prod earliest=-{timerange_hours}h ("JVM" OR "OutOfMemoryError" OR "crash")
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


def read_splunk_p0_us_infra_dashboard(query: str = "", timerange: int = 4) -> str:
    """
    P0 Streaming US infra dashboard in Splunk — same predict / z1–z4 zone logic as P0 Streaming;
    opens the US infra dashboard view.
    """
    base = read_splunk_p0_dashboard(query, timerange)
    return (
        base.replace(
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard__us_infra",
            1,
        )
        .replace("Splunk - P0 Streaming Dashboard", "Splunk - P0 Streaming US", 1)
        .replace(
            "Real-time monitoring of P0 streaming services",
            "P0 Streaming US infra — zones z1–z4 (same predict pipeline as P0 Streaming)",
            1,
        )
    )
