import os
import requests
import html
from datetime import datetime, timedelta

def get_pagerduty_incidents(query=""):
    """
    Fetches incidents from PagerDuty API
    
    Args:
        query: Search string to filter incidents (service name, incident ID, etc.)
    
    Returns:
        HTML formatted string with incident data
    """
    print(f"🚨 Fetching PagerDuty incidents for: {query}")
    
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
    
    # Add date range filter (last 7 days)
    since = (datetime.utcnow() - timedelta(days=7)).isoformat()
    
    try:
        # Fetch ALL incidents using pagination
        all_incidents = []
        offset = 0
        limit = 100
        more = True
        
        while more:
            params = {
                "sort_by": "created_at:desc",
                "limit": limit,
                "offset": offset,
                "statuses[]": ["triggered", "acknowledged", "resolved"],
                "since": since
            }
            
            # If query is provided, use it to filter
            if query:
                params["query"] = query
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code != 200:
                return f"<p style='color: #f56565;'>⚠️ PagerDuty API Error {response.status_code}: {response.reason}</p>"
            
            data = response.json()
            batch_incidents = data.get("incidents", [])
            all_incidents.extend(batch_incidents)
            
            # Check if there are more incidents to fetch
            more = data.get("more", False)
            offset += limit
            
            # Continue fetching all incidents without artificial limits
            if offset >= 10000:  # Safety limit to prevent truly infinite loops
                print(f"⚠️ Reached safety limit of 10000 incidents")
                break
        
        incidents = all_incidents
        print(f"✅ Fetched {len(incidents)} total incidents from PagerDuty")
        
        if not incidents:
            return f"<p style='color: #fbbf24;'>ℹ️ No incidents found{' for: <strong>' + html.escape(query) + '</strong>' if query else ''}.</p>"
        
        # Group incidents by status
        triggered = [i for i in incidents if i.get("status") == "triggered"]
        acknowledged = [i for i in incidents if i.get("status") == "acknowledged"]
        resolved = [i for i in incidents if i.get("status") == "resolved"]
        
        # Build HTML output with summary
        html_output = f"<h2 style='color: #10b981;'>🚨 PagerDuty Alerts - Last 7 Days</h2>"
        html_output += f"<div style='background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin-bottom: 20px;'>"
        html_output += f"<h3 style='margin: 0 0 10px 0;'>📊 Summary</h3>"
        html_output += f"<p style='margin: 5px 0;'><strong>Total Incidents:</strong> {len(incidents)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #ef4444;'><strong>🔴 Triggered:</strong> {len(triggered)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #f59e0b;'><strong>🟡 Acknowledged:</strong> {len(acknowledged)}</p>"
        html_output += f"<p style='margin: 5px 0; color: #10b981;'><strong>🟢 Resolved:</strong> {len(resolved)}</p>"
        html_output += f"</div>"
        
        if query:
            html_output += f"<p style='color: #60a5fa;'>🔍 Filter applied: <strong>{html.escape(query)}</strong></p>"
        
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
                    <th style='padding: 10px; text-align: left;'>Assigned To</th>
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
            
            # Get assigned user
            assignments = incident.get("assignments", [])
            if assignments:
                assignee = html.escape(assignments[0].get("assignee", {}).get("summary", "Unassigned"))
            else:
                assignee = "Unassigned"
            
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
                </tr>
            """
        
        html_output += """
            </tbody>
        </table>
        """
        
        return html_output
        
    except requests.exceptions.Timeout:
        return "<p style='color: #f56565;'>⚠️ PagerDuty API request timed out. Please try again.</p>"
    except requests.exceptions.RequestException as e:
        return f"<p style='color: #f56565;'>⚠️ Error connecting to PagerDuty API: {html.escape(str(e))}</p>"
    except Exception as e:
        return f"<p style='color: #f56565;'>⚠️ Unexpected error: {html.escape(str(e))}</p>"
