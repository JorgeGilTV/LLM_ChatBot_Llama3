"""
GRM Calendar Deployments Tool
Fetches upcoming deployments from Confluence GRM Calendar
"""
import os
import re
import requests
import html
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DEPLOYMENT_KEYWORDS = (
    "deployment",
    "deploy",
    "despliegue",
    "release",
    "próximo",
    "proximas",
    "pasados",
    "pasado",
    "past",
    "anteriores",
    "previous",
    "scheduled",
    "programado",
    "calendario",
    "calendar",
    "grm",
)


def is_grm_deployment_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    ql = question.lower()
    return any(kw in ql for kw in DEPLOYMENT_KEYWORDS)


def parse_grm_deployment_query(question: str) -> tuple[str, int]:
    """
    Parse natural-language deployment questions.
    Returns (query_param, timerange_hours) where negative hours = past window.
    query_param may be 'limit:N' or a service filter substring.
    """
    question_lower = (question or "").lower()
    past_keywords = (
        "past",
        "pasado",
        "pasados",
        "anteriores",
        "previous",
        "último",
        "ultimos",
        "last",
    )
    is_past_query = any(kw in question_lower for kw in past_keywords)
    limit_count = None

    timerange_match = re.search(
        r"(\d+)\s*(hora|hour|horas|hours|day|days|dia|dias|día|días)",
        question_lower,
    )
    if timerange_match:
        timerange_hours = int(timerange_match.group(1))
        unit = timerange_match.group(2)
        if "day" in unit or "dia" in unit or "día" in unit:
            timerange_hours *= 24
    else:
        number_match = re.search(
            r"(?:past|last|próximos?|proximos|últimos?|ultimos|next)\s+(\d+)",
            question_lower,
        )
        if number_match:
            limit_count = int(number_match.group(1))
            timerange_hours = 72
        else:
            generic_plural_keywords = (
                "deployments",
                "despliegues",
                "releases",
                "últimos",
                "ultimos",
                "past",
                "próximos",
                "proximos",
            )
            if any(kw in question_lower for kw in generic_plural_keywords):
                timerange_hours = 96
                limit_count = 4 if is_past_query else 5
            else:
                timerange_hours = 24

    if is_past_query:
        timerange_hours = -timerange_hours

    query_param = f"limit:{limit_count}" if limit_count else ""
    return query_param, timerange_hours


def get_grm_deployments_mcp(
    question: str = "",
    query: str = "",
    timerange_hours: int | None = None,
) -> str:
    """MCP entry point: structured args or natural-language question."""
    if (question or "").strip() and query == "" and timerange_hours is None:
        query_param, hours = parse_grm_deployment_query(question)
        return get_grm_deployments(query_param, hours)
    hours = 24 if timerange_hours is None else timerange_hours
    return get_grm_deployments(query or "", hours)


# Local /api/deployments/upcoming can take 25s+25s (two Confluence attempts) plus parsing — keep above that.
def _internal_deployments_api_url() -> str:
    port = (os.getenv("PORT") or "8080").strip()
    return f"http://127.0.0.1:{port}/api/deployments/upcoming"


def _internal_deployments_http_timeout():
    connect_s = float(os.getenv("DEPLOYMENTS_INTERNAL_HTTP_CONNECT_TIMEOUT", "5"))
    read_s = float(os.getenv("DEPLOYMENTS_INTERNAL_HTTP_READ_TIMEOUT", "90"))
    return (connect_s, read_s)


def get_grm_deployments(query: str = "", timerange_hours: int = 24) -> str:
    """
    Get deployments from GRM Calendar (Confluence).
    
    Args:
        query: Service name to filter (optional)
        timerange_hours: Number of hours to look ahead/back
                        Positive = future deployments (e.g., 24 = next 24 hours)
                        Negative = past deployments (e.g., -24 = last 24 hours)
    
    Returns:
        HTML formatted list of deployments
    """
    print("=" * 80)
    
    is_past = timerange_hours < 0
    abs_hours = abs(timerange_hours)
    
    if is_past:
        print(f"📅 GRM Calendar - Past Deployments")
        print(f"📝 Query: {query if query else 'All services'}")
        print(f"⏰ Time range: Last {abs_hours} hours")
    else:
        print(f"📅 GRM Calendar - Upcoming Deployments")
        print(f"📝 Query: {query if query else 'All services'}")
        print(f"⏰ Time range: Next {abs_hours} hours")
    
    try:
        # Call the internal API endpoint that already fetches deployments
        # This makes a local request to our Flask app's /api/deployments/upcoming endpoint
        response = requests.get(
            _internal_deployments_api_url(),
            timeout=_internal_deployments_http_timeout(),
        )
        
        if response.status_code != 200:
            return f"""
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>Error fetching deployments:</strong> API returned status {response.status_code}
                </p>
            </div>
            """
        
        data = response.json()
        deployments = data.get('deployments', [])
        
        # Check if query contains limit parameter (e.g., "limit:3")
        limit_count = None
        if query and query.strip():
            query_lower = query.strip().lower()
            
            # Extract limit if present
            if query_lower.startswith('limit:'):
                try:
                    limit_count = int(query_lower.split(':')[1])
                    print(f"🔢 Will limit results to {limit_count} deployments")
                    query = ""  # Clear query after extracting limit
                except:
                    pass
            else:
                # Filter by service name
                deployments = [
                    d for d in deployments 
                    if query_lower in d.get('service', '').lower()
                ]
        
        # Filter by time range
        now = datetime.now(timezone.utc)
        filtered_deployments = []
        
        for deploy in deployments:
            try:
                deploy_time = datetime.fromisoformat(deploy.get('timestamp', ''))
                hours_diff = (deploy_time - now).total_seconds() / 3600
                
                if is_past:
                    # For past deployments: include those in the past within the range
                    # hours_diff will be negative for past deployments
                    if timerange_hours <= hours_diff <= 0:
                        deploy['hours_from_now'] = hours_diff
                        deploy['hours_ago'] = abs(hours_diff)
                        filtered_deployments.append(deploy)
                else:
                    # For future deployments: include those in the future within the range
                    if 0 <= hours_diff <= timerange_hours:
                        deploy['hours_from_now'] = hours_diff
                        filtered_deployments.append(deploy)
            except:
                continue
        
        # Sort by time
        if is_past:
            # For past deployments, show most recent first (reverse order)
            filtered_deployments.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        else:
            # For future deployments, show soonest first
            filtered_deployments.sort(key=lambda x: x.get('timestamp', ''))
        
        # Apply limit if specified
        if limit_count and limit_count > 0:
            filtered_deployments = filtered_deployments[:limit_count]
            print(f"📊 Limited results to {len(filtered_deployments)} deployment(s)")
        
        # Build HTML output
        if is_past:
            title = "📅 GRM Calendar - Past Deployments"
            subtitle = f"Deployments from the last {abs_hours} hours"
        else:
            title = "📅 GRM Calendar - Upcoming Deployments"
            subtitle = f"Deployments scheduled for the next {abs_hours} hours"
        
        output = f"""
        <div style='background: linear-gradient(135deg, {"#6b7280 0%, #4b5563 100%" if is_past else "#10b981 0%, #059669 100%"}); 
                    padding: 12px; 
                    border-radius: 6px; 
                    margin: 8px 0;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>
                {title}
            </h2>
            <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
                {subtitle}
                {f" | 🔍 Filter: {html.escape(query)}" if query else ""}
            </p>
            <p style='margin: 0; font-size: 11px; opacity: 0.9;'>
                📊 Found {len(filtered_deployments)} deployment(s)
            </p>
        </div>
        """
        
        if not filtered_deployments:
            no_deploy_msg = "No deployments found" if is_past else "No deployments scheduled"
            output += f"""
            <div style='background-color: #d1fae5; padding: 12px; border-left: 4px solid #10b981; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #065f46;'>
                    ✅ <strong>{no_deploy_msg}</strong> for the specified time range.
                </p>
            </div>
            """
        else:
            time_column_header = "Ago" if is_past else "In"
            header_color = "#6b7280" if is_past else "#10b981"
            
            output += f"""
            <div style='margin: 12px 0;'>
                <table style='width: 100%; border-collapse: collapse; font-size: 13px;'>
                    <thead>
                        <tr style='background-color: {header_color}; color: white;'>
                            <th style='padding: 10px; text-align: left; border: 1px solid #ddd;'>Time</th>
                            <th style='padding: 10px; text-align: left; border: 1px solid #ddd;'>Service/Component</th>
                            <th style='padding: 10px; text-align: left; border: 1px solid #ddd;'>{time_column_header}</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for deploy in filtered_deployments:
                date = deploy.get('date', 'Unknown')
                time_str = deploy.get('time', 'Unknown')
                service = html.escape(deploy.get('service', 'Unknown Service'))
                
                if is_past:
                    # For past deployments
                    hours_ago = deploy.get('hours_ago', 0)
                    if hours_ago < 1:
                        time_display = f"{int(hours_ago * 60)} minutes ago"
                    else:
                        time_display = f"{hours_ago:.1f} hours ago"
                    row_color = "#f3f4f6"  # Gray for past
                else:
                    # For future deployments
                    hours_from_now = deploy.get('hours_from_now', 0)
                    if hours_from_now < 1:
                        time_display = f"{int(hours_from_now * 60)} minutes"
                        row_color = "#fee2e2"  # Red tint for very soon
                    elif hours_from_now < 3:
                        time_display = f"{hours_from_now:.1f} hours"
                        row_color = "#fef3c7"  # Yellow tint for soon
                    else:
                        time_display = f"{hours_from_now:.1f} hours"
                        row_color = "#f3f4f6"  # Gray for later
                
                output += f"""
                    <tr style='border-bottom: 1px solid #ddd; background-color: {row_color};'>
                        <td style='padding: 8px; border: 1px solid #ddd;'>
                            <strong>{date}</strong><br>
                            <span style='font-size: 11px; color: #666;'>{time_str}</span>
                        </td>
                        <td style='padding: 8px; border: 1px solid #ddd;'>{service}</td>
                        <td style='padding: 8px; border: 1px solid #ddd;'>
                            <strong>{time_display}</strong>
                        </td>
                    </tr>
                """
            
            output += """
                    </tbody>
                </table>
            </div>
            """
        
        output += """
        <div style='margin: 8px 0; padding: 6px; background-color: #e0e7ff; border-left: 3px solid #6366f1; border-radius: 4px;'>
            <p style='margin: 0; font-size: 11px; color: #4338ca;'>
                ℹ️ <strong>Source:</strong> Confluence GRM Calendar • 
                <a href='https://arlo.atlassian.net/wiki/spaces/GRM/pages/153256867/GRM+Calendar' 
                   target='_blank' style='color: #4338ca;'>View Full Calendar →</a>
            </p>
        </div>
        """
        
        return output

    except requests.exceptions.Timeout:
        port = (os.getenv("PORT") or "8080").strip()
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>GRM Calendar service did not respond in time.</strong><br>
                The local deployments API on port {html.escape(port)} is still waiting on Confluence
                (slow Team Calendar). If this persists, increase <code>DEPLOYMENTS_INTERNAL_HTTP_READ_TIMEOUT</code>
                or check Confluence/API token.
            </p>
        </div>
        """

    except requests.exceptions.ConnectionError:
        port = (os.getenv("PORT") or "8080").strip()
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Cannot connect to deployments API.</strong><br>
                Make sure the Flask server is listening on port {html.escape(port)} (env <code>PORT</code>).
            </p>
        </div>
        """
    except Exception as e:
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Error fetching deployments:</strong> {html.escape(str(e))}
            </p>
        </div>
        """
