import os
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

from tools.pagerduty_team import (
    fetch_incidents_touched_by_team,
    normalize_pagerduty_shift,
    pagerduty_shift_label,
    pagerduty_user_ids_for_filter,
)

def get_pagerduty_insights(query="", shift: str = "", team_only: bool = False):
    """
    Fetches incident activity insights from PagerDuty API
    
    Args:
        query: Optional filter or time range
    
    Returns:
        HTML formatted string with insights and detailed metrics
    """
    print(f"🔍 Fetching PagerDuty Insights data...")
    
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
    
    # Calculate time range (last 30 days)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=30)
    
    # Format times for API
    since = start_time.isoformat() + "Z"
    until = end_time.isoformat() + "Z"
    
    try:
        incidents_url = "https://api.pagerduty.com/incidents"
        active_shift = normalize_pagerduty_shift(shift)
        filter_ids = pagerduty_user_ids_for_filter(active_shift or None, team_only=team_only)
        if filter_ids:
            days = 30
            incidents = fetch_incidents_touched_by_team(
                api_token, days=days, user_ids=filter_ids
            )
            total_count = len(incidents)
            label = pagerduty_shift_label(active_shift) if active_shift else "all shifts"
            print(f"✅ PagerDuty Insights ({label}): {total_count} incident(s) touched in last {days} days")
        else:
            all_incidents = []
            offset = 0
            limit = 100
            more = True

            while more:
                incidents_params = {
                    "since": since,
                    "until": until,
                    "limit": limit,
                    "offset": offset,
                    "total": "true",
                    "time_zone": "UTC"
                }

                incidents_response = requests.get(incidents_url, headers=headers, params=incidents_params, timeout=15)

                if incidents_response.status_code != 200:
                    return f"<p style='color: #f56565;'>⚠️ PagerDuty API Error {incidents_response.status_code}: {incidents_response.reason}</p>"

                incidents_data = incidents_response.json()
                batch_incidents = incidents_data.get("incidents", [])
                all_incidents.extend(batch_incidents)

                more = incidents_data.get("more", False)
                offset += limit

                if offset >= 10000:
                    print(f"⚠️ PagerDuty Insights: Reached safety limit of 10000 incidents")
                    break

            incidents = all_incidents
            total_count = len(incidents)
            print(f"✅ PagerDuty Insights: Fetched {total_count} total incidents")
        
        # Get services list for additional context
        services_url = "https://api.pagerduty.com/services"
        services_params = {"limit": 100}
        services_response = requests.get(services_url, headers=headers, params=services_params, timeout=10)
        services = services_response.json().get("services", []) if services_response.status_code == 200 else []
        
        # Analyze incidents
        triggered = [i for i in incidents if i.get("status") == "triggered"]
        acknowledged = [i for i in incidents if i.get("status") == "acknowledged"]
        resolved = [i for i in incidents if i.get("status") == "resolved"]
        
        # Calculate resolution times
        resolution_times = []
        for incident in resolved:
            created = incident.get("created_at")
            resolved_at = incident.get("last_status_change_at")
            if created and resolved_at:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    resolved_dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
                    resolution_time = (resolved_dt - created_dt).total_seconds() / 60  # minutes
                    resolution_times.append(resolution_time)
                except:
                    pass
        
        # Calculate average resolution time
        avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else 0
        
        # Analyze by day of week
        day_counts = defaultdict(int)
        for incident in incidents:
            created = incident.get("created_at")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    day_name = dt.strftime("%A")
                    day_counts[day_name] += 1
                except:
                    pass
        
        # Analyze by hour
        hour_counts = defaultdict(int)
        for incident in incidents:
            created = incident.get("created_at")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    hour_counts[dt.hour] += 1
                except:
                    pass
        
        # Service with most incidents
        service_counts = defaultdict(int)
        service_names = {}
        for incident in incidents:
            service = incident.get("service", {})
            service_id = service.get("id")
            service_name = service.get("summary", "Unknown")
            if service_id:
                service_counts[service_id] += 1
                service_names[service_id] = service_name
        
        top_service_id = max(service_counts, key=service_counts.get) if service_counts else None
        top_service_name = service_names.get(top_service_id, "N/A") if top_service_id else "N/A"
        top_service_count = service_counts.get(top_service_id, 0) if top_service_id else 0
        
        # Users with most incidents assigned
        user_counts = defaultdict(int)
        user_names = {}
        for incident in incidents:
            assignments = incident.get("assignments", [])
            for assignment in assignments:
                assignee = assignment.get("assignee", {})
                user_id = assignee.get("id")
                user_name = assignee.get("summary", "Unknown")
                if user_id:
                    user_counts[user_id] += 1
                    user_names[user_id] = user_name
        
        top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Busiest day
        busiest_day = max(day_counts, key=day_counts.get) if day_counts else "N/A"
        busiest_day_count = day_counts.get(busiest_day, 0) if busiest_day != "N/A" else 0
        
        # Busiest hour
        busiest_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
        busiest_hour_count = hour_counts.get(busiest_hour, 0) if busiest_hour else 0
        
        # Days order for chart
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        days_data = [day_counts.get(day, 0) for day in days_order]
        
        # Hours data for chart (24 hours)
        hours_data = [hour_counts.get(h, 0) for h in range(24)]
        
        # Build HTML output
        team_note = ""
        if active_shift:
            team_note = (
                f"<p style='margin: 0 0 15px 0; font-size: 13px; color: #64748b;'>"
                f"Filtered to <strong>{pagerduty_shift_label(active_shift)}</strong> "
                f"(incidents touched by that shift crew)</p>"
            )
        elif team_only and filter_ids:
            team_note = (
                "<p style='margin: 0 0 15px 0; font-size: 13px; color: #64748b;'>"
                "Filtered to <strong>any configured shift crew</strong></p>"
            )
        html_output = f"""
        <h2 style='color: #8b5cf6;'>🔍 PagerDuty Insights - Incident Activity Report</h2>
        {team_note}
        
        <div style='background: linear-gradient(135deg, #8b5cf6 0%, #ec4899 100%); padding: 25px; border-radius: 12px; margin-bottom: 25px; color: white;'>
            <h3 style='margin: 0 0 20px 0; color: white;'>📊 Key Insights (Last 30 Days)</h3>
            <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px;'>
                <div style='background: rgba(255,255,255,0.15); padding: 18px; border-radius: 10px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 38px; font-weight: bold;'>{total_count}</div>
                    <div style='font-size: 14px; opacity: 0.95; margin-top: 5px;'>Total Incidents</div>
                </div>
                <div style='background: rgba(255,255,255,0.15); padding: 18px; border-radius: 10px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 38px; font-weight: bold;'>{avg_resolution:.1f}</div>
                    <div style='font-size: 14px; opacity: 0.95; margin-top: 5px;'>Avg Resolution (min)</div>
                </div>
                <div style='background: rgba(255,255,255,0.15); padding: 18px; border-radius: 10px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 20px; font-weight: bold; margin-top: 8px;'>{busiest_day}</div>
                    <div style='font-size: 14px; opacity: 0.95; margin-top: 5px;'>Busiest Day ({busiest_day_count})</div>
                </div>
                <div style='background: rgba(255,255,255,0.15); padding: 18px; border-radius: 10px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 38px; font-weight: bold;'>{busiest_hour if busiest_hour is not None else 'N/A'}:00</div>
                    <div style='font-size: 14px; opacity: 0.95; margin-top: 5px;'>Busiest Hour ({busiest_hour_count})</div>
                </div>
            </div>
        </div>
        
        <div style='background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin-bottom: 20px; border-radius: 6px;'>
            <h4 style='margin: 0 0 10px 0; color: #92400e;'>🎯 Top Service with Most Incidents</h4>
            <p style='margin: 0; color: #78350f; font-size: 16px;'>
                <strong>{top_service_name}</strong> - {top_service_count} incidents
            </p>
        </div>
        
        <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;'>
            <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
                <h4 style='margin: 0 0 15px 0; color: #1f2937;'>📅 Incidents by Day of Week</h4>
                <canvas id='days-chart' style='max-height: 300px;'></canvas>
            </div>
            
            <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
                <h4 style='margin: 0 0 15px 0; color: #1f2937;'>🕐 Incidents by Hour of Day</h4>
                <canvas id='hours-chart' style='max-height: 300px;'></canvas>
            </div>
        </div>
        
        <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px;'>
            <h4 style='margin: 0 0 15px 0; color: #1f2937;'>👥 Top 5 Users by Incident Count</h4>
            <table style='width: 100%; border-collapse: collapse;'>
                <thead>
                    <tr style='background-color: #f3f4f6;'>
                        <th style='padding: 12px; text-align: left; border-bottom: 2px solid #e5e7eb;'>Rank</th>
                        <th style='padding: 12px; text-align: left; border-bottom: 2px solid #e5e7eb;'>User</th>
                        <th style='padding: 12px; text-align: right; border-bottom: 2px solid #e5e7eb;'>Incidents Assigned</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for idx, (user_id, count) in enumerate(top_users, 1):
            user_name = user_names.get(user_id, "Unknown")
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][idx-1] if idx <= 5 else ""
            html_output += f"""
                    <tr style='border-bottom: 1px solid #e5e7eb;'>
                        <td style='padding: 12px;'>{medal} {idx}</td>
                        <td style='padding: 12px;'>{user_name}</td>
                        <td style='padding: 12px; text-align: right; font-weight: bold; color: #6366f1;'>{count}</td>
                    </tr>
            """
        
        html_output += """
                </tbody>
            </table>
        </div>
        
        <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px;'>
            <h4 style='margin: 0 0 15px 0; color: #1f2937;'>📈 Resolution Time Distribution</h4>
        """
        
        if resolution_times:
            # Calculate percentiles
            sorted_times = sorted(resolution_times)
            p50 = sorted_times[len(sorted_times) // 2] if sorted_times else 0
            p90 = sorted_times[int(len(sorted_times) * 0.9)] if len(sorted_times) > 1 else 0
            p95 = sorted_times[int(len(sorted_times) * 0.95)] if len(sorted_times) > 1 else 0
            
            html_output += f"""
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px;'>
                <div style='text-align: center; padding: 15px; background: #f0fdf4; border-radius: 8px;'>
                    <div style='font-size: 28px; font-weight: bold; color: #16a34a;'>{p50:.1f}</div>
                    <div style='font-size: 13px; color: #166534;'>P50 (Median) minutes</div>
                </div>
                <div style='text-align: center; padding: 15px; background: #fef3c7; border-radius: 8px;'>
                    <div style='font-size: 28px; font-weight: bold; color: #ca8a04;'>{p90:.1f}</div>
                    <div style='font-size: 13px; color: #854d0e;'>P90 minutes</div>
                </div>
                <div style='text-align: center; padding: 15px; background: #fef2f2; border-radius: 8px;'>
                    <div style='font-size: 28px; font-weight: bold; color: #dc2626;'>{p95:.1f}</div>
                    <div style='font-size: 13px; color: #991b1b;'>P95 minutes</div>
                </div>
            </div>
            """
        else:
            html_output += "<p style='color: #6b7280;'>No resolution time data available.</p>"
        
        html_output += """
        </div>
        
        <script>
        (function() {
            // Days of Week Chart
            const daysCtx = document.getElementById('days-chart');
            if (daysCtx) {
                new Chart(daysCtx, {
                    type: 'bar',
                    data: {
                        labels: """ + json.dumps(days_order) + """,
                        datasets: [{
                            label: 'Incidents',
                            data: """ + json.dumps(days_data) + """,
                            backgroundColor: 'rgba(139, 92, 246, 0.8)',
                            borderColor: 'rgba(139, 92, 246, 1)',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: { precision: 0 }
                            }
                        }
                    }
                });
            }
            
            // Hours Chart
            const hoursCtx = document.getElementById('hours-chart');
            if (hoursCtx) {
                new Chart(hoursCtx, {
                    type: 'line',
                    data: {
                        labels: ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22', '23'],
                        datasets: [{
                            label: 'Incidents',
                            data: """ + json.dumps(hours_data) + """,
                            fill: true,
                            backgroundColor: 'rgba(236, 72, 153, 0.2)',
                            borderColor: 'rgba(236, 72, 153, 1)',
                            borderWidth: 2,
                            tension: 0.4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: { precision: 0 }
                            },
                            x: {
                                title: {
                                    display: true,
                                    text: 'Hour (UTC)'
                                }
                            }
                        }
                    }
                });
            }
        })();
        </script>
        
        <div style='background: #f3f4f6; padding: 15px; border-radius: 8px; margin-top: 20px;'>
            <p style='margin: 0; color: #6b7280; font-size: 13px;'>
                ℹ️ <strong>Insights Report</strong> - Data covers the last 30 days. All times are in UTC. 
                Resolution time is calculated from incident creation to resolution.
            </p>
        </div>
        """
        
        return html_output
        
    except requests.exceptions.Timeout:
        return "<p style='color: #f56565;'>⚠️ PagerDuty API request timed out. Please try again.</p>"
    except requests.exceptions.RequestException as e:
        return f"<p style='color: #f56565;'>⚠️ Error connecting to PagerDuty API: {str(e)}</p>"
    except Exception as e:
        return f"<p style='color: #f56565;'>⚠️ Unexpected error: {str(e)}</p>"
