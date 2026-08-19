import os
import requests
import html
from datetime import datetime, timedelta

from tools.service_query import extract_service_name_from_query
from tools.pagerduty_team import (
    PAGERDUTY_SHIFTS,
    enrich_incidents_custom_fields,
    fetch_all_incidents,
    fetch_incidents_touched_by_team,
    incident_root_cause,
    normalize_pagerduty_shift,
    pagerduty_incidents_lookback_days,
    pagerduty_shift_label,
    pagerduty_user_ids_for_filter,
)

_PD_FUZZY_PART_BLOCKLIST = frozenset(
    {
        "backend", "nginx", "device", "oauth", "partner", "proxy", "logger",
        "hmsweb", "secret", "mqtt", "broker", "privacy", "registration",
        "support", "discovery", "directory", "presence", "messaging", "history",
        "advisor", "geolocation", "mediamigrationscheduler", "automation", "arlo",
    }
)


def _pd_incident_search_blob(incident: dict) -> str:
    chunks = []
    for key in ("title", "description", "summary"):
        v = incident.get(key)
        if isinstance(v, str) and v.strip():
            chunks.append(v.lower())
    body = incident.get("body")
    if isinstance(body, dict):
        det = body.get("details")
        if isinstance(det, str) and det.strip():
            chunks.append(det.lower())
    service_obj = incident.get("service")
    if isinstance(service_obj, dict):
        for k in ("summary", "name", "description"):
            v = service_obj.get(k)
            if isinstance(v, str) and v.strip():
                chunks.append(v.lower())
    return " ".join(chunks)


def _incident_matches_service(incident: dict, service_name: str) -> bool:
    needle = (service_name or "").strip().lower()
    if not needle:
        return True

    title = (incident.get("title") or "").lower()
    service_obj = incident.get("service") or {}
    svc = (service_obj.get("summary") or service_obj.get("name") or "").lower()
    blob = _pd_incident_search_blob(incident)

    candidates = {needle}
    if needle.startswith("backend-"):
        candidates.add(needle[8:])
    else:
        candidates.add(f"backend-{needle}")

    for token in candidates:
        if token and (token in title or token in svc or token in blob):
            return True

    for part in needle.split("-"):
        pl = part.lower()
        if len(pl) > 4 and pl not in _PD_FUZZY_PART_BLOCKLIST and pl in title:
            return True

    significant = [
        p.lower()
        for p in needle.split("-")
        if len(p) >= 6 and p.lower() not in _PD_FUZZY_PART_BLOCKLIST
    ]
    for part in significant:
        if part in blob or part in title or part in svc:
            return True

    return False


def get_pagerduty_incidents(
    query="",
    shift: str = "",
    team_only: bool = False,
    missing_root_cause: bool = False,
):
    """
    Fetches incidents from PagerDuty API

    Args:
        query: Search string to filter incidents (service name, incident ID, etc.)
        shift: shift1 | shift2 | shift3 — filter to that shift's on-call crew
        team_only: Legacy — when shift empty, union of all shift crews
        missing_root_cause: When True, only incidents with empty root_cause custom field

    Returns:
        HTML formatted string with incident data
    """
    active_shift = normalize_pagerduty_shift(shift)
    filter_ids = pagerduty_user_ids_for_filter(active_shift or None, team_only=team_only)
    print(
        f"🚨 Fetching PagerDuty incidents for: {query!r} "
        f"(shift={active_shift or 'all'}, missing_rca={missing_root_cause})"
    )
    filter_service = extract_service_name_from_query(query)
    if (query or "").strip() and filter_service != (query or "").strip().lower():
        print(f"   → Service filter: {filter_service}")
    
    # Get PagerDuty API token from environment
    api_token = os.getenv("PAGERDUTY_API_TOKEN")
    if not api_token:
        return "<p style='color: #f56565;'>⚠️ Error: PAGERDUTY_API_TOKEN not set in environment variables.</p>"
    
    # PagerDuty API configuration
    headers = {
        "Authorization": f"Token token={api_token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json"
    }
    
    # API endpoint for incidents
    url = "https://api.pagerduty.com/incidents"
    
    # Date range (default 15 days — PAGERDUTY_INCIDENTS_LOOKBACK_DAYS)
    lookback_days = pagerduty_incidents_lookback_days()
    since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    
    try:
        if filter_ids:
            shift_label = pagerduty_shift_label(active_shift) if active_shift else "all shifts"
            all_incidents = fetch_incidents_touched_by_team(
                api_token, days=lookback_days, user_ids=filter_ids
            )
            print(
                f"✅ PagerDuty ({shift_label}): "
                f"{len(all_incidents)} incident(s) touched in last {lookback_days} days"
            )
        else:
            all_incidents = fetch_all_incidents(api_token, days=lookback_days)
            print(f"✅ Fetched {len(all_incidents)} total incidents from PagerDuty ({lookback_days}d)")

        all_incidents = enrich_incidents_custom_fields(api_token, all_incidents)

        incidents = all_incidents
        if missing_root_cause:
            incidents = [i for i in incidents if not incident_root_cause(i)[0]]
            print(f"🔍 Missing root cause filter: {len(incidents)} incident(s)")
        if filter_service:
            incidents = [i for i in incidents if _incident_matches_service(i, filter_service)]
            print(
                f"🔍 Display filter for service '{filter_service}': "
                f"{len(incidents)} of {len(all_incidents)} incidents"
            )

        if not incidents:
            if filter_service and all_incidents:
                return (
                    f"<p style='color: #fbbf24;'>ℹ️ No incidents matched service "
                    f"<strong>{html.escape(filter_service)}</strong> "
                    f"(searched {len(all_incidents)} incidents in the last {lookback_days} days).</p>"
                )
            return f"<p style='color: #fbbf24;'>ℹ️ No incidents found{' for: <strong>' + html.escape(query) + '</strong>' if query else ''}.</p>"
        
        # Group incidents by status
        triggered = [i for i in incidents if i.get("status") == "triggered"]
        acknowledged = [i for i in incidents if i.get("status") == "acknowledged"]
        resolved = [i for i in incidents if i.get("status") == "resolved"]
        
        # Identify recently resolved incidents (last 24 hours)
        now = datetime.utcnow()
        recent_cutoff = now - timedelta(hours=24)
        recently_resolved = []
        
        for incident in resolved:
            try:
                resolved_at_str = incident.get("last_status_change_at", "")
                if resolved_at_str:
                    resolved_at = datetime.fromisoformat(resolved_at_str.replace("Z", "+00:00"))
                    if resolved_at.replace(tzinfo=None) >= recent_cutoff:
                        recently_resolved.append(incident)
            except:
                pass
        
        # Build HTML output with summary
        safe_query = html.escape(query or "")
        safe_shift = html.escape(active_shift)
        shift_buttons = ""
        for mode in PAGERDUTY_SHIFTS:
            label = pagerduty_shift_label(mode)
            on = active_shift == mode
            shift_buttons += (
                f"<button type='button' class='pd-filter-btn{' pd-filter-btn--on' if on else ''}' "
                f"data-pd-filter='{mode}' style='padding:6px 12px;border-radius:6px;border:1px solid #cbd5e0;"
                f"background:{'#dbeafe' if on else '#fff'};cursor:pointer;font-size:12px;font-weight:600;'>"
                f"👥 {html.escape(label)}</button>"
            )
        html_output = f"""<div class="pd-query-wrap" data-pd-query="{safe_query}" data-pd-shift="{safe_shift}">
        <div class="pd-query-toolbar" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px 0;">
            {shift_buttons}
            <button type="button" class="pd-filter-btn{' pd-filter-btn--on' if missing_root_cause else ''}" data-pd-filter="missing_rca" style="padding:6px 12px;border-radius:6px;border:1px solid #cbd5e0;background:{'#fef3c7' if missing_root_cause else '#fff'};cursor:pointer;font-size:12px;font-weight:600;">
                ⚠️ Missing root cause
            </button>
            <span style="font-size:11px;color:#64748b;">Shift = crew filter · click again to show all · last {lookback_days} days</span>
        </div>
        <div id="pd-query-results">"""
        html_output += f"<h2 style='color: #10b981;'>🚨 PagerDuty Alerts - Last {lookback_days} Days</h2>"
        if active_shift:
            html_output += (
                f"<p style='margin: 0 0 8px 0; font-size: 12px; color: #64748b;'>"
                f"Showing incidents touched by <strong>{html.escape(pagerduty_shift_label(active_shift))}</strong></p>"
            )
        elif team_only and filter_ids:
            html_output += (
                f"<p style='margin: 0 0 8px 0; font-size: 12px; color: #64748b;'>"
                f"Showing incidents touched by <strong>any configured shift crew</strong></p>"
            )
        else:
            html_output += (
                "<p style='margin: 0 0 8px 0; font-size: 12px; color: #64748b;'>"
                "Showing <strong>all account</strong> incidents</p>"
            )
        if missing_root_cause:
            html_output += (
                "<p style='margin: 0 0 8px 0; font-size: 12px; color: #b45309;'>"
                "Filtered to incidents <strong>without</strong> Root cause filled</p>"
            )
        html_output += f"<div style='background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin-bottom: 20px;'>"
        html_output += f"<h3 style='margin: 0 0 10px 0;'>📊 Summary</h3>"
        if filter_service:
            html_output += (
                f"<p style='margin: 5px 0; font-size: 12px; color: #64748b;'>"
                f"Showing <strong>{len(incidents)}</strong> incident(s) for "
                f"<strong>{html.escape(filter_service)}</strong> "
                f"(from {len(all_incidents)} fetched in last {lookback_days} days)</p>"
            )
        html_output += f"<p style='margin: 5px 0;'><strong>Total Incidents:</strong> {len(incidents)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #ef4444;'><strong>🔴 Triggered:</strong> {len(triggered)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #f59e0b;'><strong>🟡 Acknowledged:</strong> {len(acknowledged)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #10b981;'><strong>🟢 Resolved:</strong> {len(resolved)}</p>"
        
        # Highlight recently resolved incidents
        if recently_resolved:
            html_output += f"<div style='background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 10px; margin-top: 10px; border-radius: 4px;'>"
            html_output += f"<p style='margin: 0; color: #92400e; font-weight: bold;'>⚠️ Recently Resolved (Last 24h): {len(recently_resolved)}</p>"
            html_output += f"<p style='margin: 5px 0 0 0; font-size: 12px; color: #78350f;'>Check for recurring patterns or potential instability</p>"
            html_output += f"</div>"
        
        html_output += f"</div>"
        
        if filter_service:
            html_output += (
                f"<p style='color: #60a5fa;'>🔍 Service filter: "
                f"<strong>{html.escape(filter_service)}</strong></p>"
            )
        
        # Organize incidents by status (Triggered → Acknowledged → Resolved)
        sorted_incidents = triggered + acknowledged + resolved
        
        html_output += """
        <table border='1' style='border-collapse: collapse; width: 100%; margin-top: 10px;'>
            <thead style='background-color: #1f2937; color: white;'>
                <tr>
                    <th style='padding: 10px; text-align: left;'>Status</th>
                    <th style='padding: 10px; text-align: left;'>Incident #</th>
                    <th style='padding: 10px; text-align: left;'>Title</th>
                    <th style='padding: 10px; text-align: left;'>Service</th>
                    <th style='padding: 10px; text-align: left;'>Urgency</th>
                    <th style='padding: 10px; text-align: left;'>Created</th>
                    <th style='padding: 10px; text-align: left;'>NOC Team</th>
                    <th style='padding: 10px; text-align: left;'>Root Cause</th>
                </tr>
            </thead>
            <tbody>
        """
        
        for incident in sorted_incidents:
            status = incident.get("status", "unknown")
            status_color = {
                "triggered": "#ef4444",
                "acknowledged": "#f59e0b",
                "resolved": "#10b981"
            }.get(status, "#6b7280")
            
            # Background color for row based on status
            row_bg = {
                "triggered": "#fef2f2",
                "acknowledged": "#fffbeb",
                "resolved": "#f0fdf4"
            }.get(status, "#ffffff")
            
            incident_number = incident.get("incident_number", "N/A")
            title = html.escape(incident.get("title", "No title"))
            service_name = html.escape(incident.get("service", {}).get("summary", "Unknown"))
            urgency = incident.get("urgency", "unknown")
            urgency_color = "#ef4444" if urgency == "high" else "#fbbf24"
            
            created_at = incident.get("created_at", "")
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_str = created_dt.strftime("%Y-%m-%d %H:%M UTC")
            except:
                created_str = created_at
            
            # NOC team member(s) who touched this incident (team mode) or current assignee
            touched = incident.get("_team_touched_by") or []
            if touched:
                assignee = html.escape(", ".join(touched))
            else:
                assignments = incident.get("assignments", [])
                if assignments:
                    assignee = html.escape(
                        assignments[0].get("assignee", {}).get("summary", "Unassigned")
                    )
                else:
                    assignee = "—"

            has_rca, rca_text = incident_root_cause(incident)
            if has_rca:
                rca_cell = (
                    f"<span style='color:#166534;font-weight:600;' title='{html.escape(rca_text)}'>"
                    f"✅ {html.escape(rca_text[:80])}{'…' if len(rca_text) > 80 else ''}</span>"
                )
            else:
                rca_cell = "<span style='color:#b45309;font-weight:600;'>⚠️ Empty</span>"
            
            # Create incident URL
            incident_url = incident.get("html_url", "#")
            
            html_output += f"""
                <tr style='border-bottom: 1px solid #e5e7eb; background-color: {row_bg};'>
                    <td style='padding: 8px;'>
                        <span style='background-color: {status_color}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;'>
                            {status.upper()}
                        </span>
                    </td>
                    <td style='padding: 8px;'>
                        <a href='{incident_url}' target='_blank' style='color: #3b82f6; text-decoration: underline; font-weight: bold;'>
                            #{incident_number}
                        </a>
                    </td>
                    <td style='padding: 8px;'>{title}</td>
                    <td style='padding: 8px;'>{service_name}</td>
                    <td style='padding: 8px;'>
                        <span style='color: {urgency_color}; font-weight: bold;'>
                            {urgency.upper()}
                        </span>
                    </td>
                    <td style='padding: 8px; white-space: nowrap;'>{created_str}</td>
                    <td style='padding: 8px;'>{assignee}</td>
                    <td style='padding: 8px; max-width: 220px;'>{rca_cell}</td>
                </tr>
            """
        
        html_output += """
            </tbody>
        </table>
        </div>
        </div>
        <script>
        (function () {
            const wrap = document.currentScript && document.currentScript.previousElementSibling;
            const root = wrap && wrap.classList && wrap.classList.contains('pd-query-wrap')
                ? wrap
                : document.querySelector('.pd-query-wrap:last-of-type');
            if (!root) return;
            let pdShift = root.getAttribute('data-pd-shift') || '';
            let missingRca = """ + ("true" if missing_root_cause else "false") + """;
            const q = root.getAttribute('data-pd-query') || '';
            function reloadPd() {
                const params = new URLSearchParams({
                    query: q,
                    shift: pdShift || '',
                    missing_rca: missingRca ? '1' : '0',
                });
                fetch('/api/pagerduty/incidents?' + params.toString())
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.error) throw new Error(data.error);
                        const results = document.getElementById('pd-query-results');
                        if (results && data.html) {
                            const tmp = document.createElement('div');
                            tmp.innerHTML = data.html;
                            const fresh = tmp.querySelector('#pd-query-results');
                            if (fresh) {
                                results.innerHTML = fresh.innerHTML;
                            }
                            const outer = tmp.querySelector('.pd-query-wrap');
                            if (outer) {
                                pdShift = outer.getAttribute('data-pd-shift') || '';
                                root.setAttribute('data-pd-shift', pdShift);
                            }
                            tmp.querySelectorAll('script').forEach(function (oldScript) {
                                const s = document.createElement('script');
                                if (oldScript.textContent) s.textContent = oldScript.textContent;
                                document.body.appendChild(s);
                                oldScript.remove();
                            });
                        }
                    })
                    .catch(function (e) { console.error('PagerDuty reload:', e); });
            }
            root.querySelectorAll('.pd-filter-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    const f = btn.getAttribute('data-pd-filter');
                    if (f === 'missing_rca') {
                        missingRca = !missingRca;
                    } else if (f && f.indexOf('shift') === 0) {
                        pdShift = pdShift === f ? '' : f;
                        root.setAttribute('data-pd-shift', pdShift);
                    }
                    reloadPd();
                });
            });
        })();
        </script>
        """
        
        return html_output
        
    except requests.exceptions.Timeout:
        return "<p style='color: #f56565;'>⚠️ PagerDuty API request timed out. Please try again.</p>"
    except requests.exceptions.RequestException as e:
        return f"<p style='color: #f56565;'>⚠️ Error connecting to PagerDuty API: {html.escape(str(e))}</p>"
    except Exception as e:
        return f"<p style='color: #f56565;'>⚠️ Unexpected error: {html.escape(str(e))}</p>"
