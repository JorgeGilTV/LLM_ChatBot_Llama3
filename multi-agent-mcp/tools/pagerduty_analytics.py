import os
import requests
import json
from datetime import datetime, timedelta

def get_pagerduty_analytics(query=""):
    """
    Fetches analytics data from PagerDuty API and displays with charts
    
    Args:
        query: Time range filter (optional)
    
    Returns:
        HTML formatted string with analytics data and charts
    """
    print(f"📊 Fetching PagerDuty Analytics data...")
    
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
    
    # Calculate time range (last 30 days by default)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=30)
    
    # Format times for API
    since = start_time.isoformat() + "Z"
    until = end_time.isoformat() + "Z"
    
    try:
        # Get incident analytics
        analytics_url = "https://api.pagerduty.com/analytics/metrics/incidents/all"
        params = {
            "aggregate_unit": "day"
        }
        
        analytics_response = requests.get(analytics_url, headers=headers, params=params, timeout=15)
        
        # Get service analytics
        services_url = "https://api.pagerduty.com/analytics/metrics/incidents/services"
        services_response = requests.get(services_url, headers=headers, params=params, timeout=15)
        
        # Get teams analytics (if available)
        teams_url = "https://api.pagerduty.com/analytics/metrics/incidents/teams"
        teams_response = requests.get(teams_url, headers=headers, params=params, timeout=15)
        
        # Get ALL incidents for statistics using pagination
        incidents_url = "https://api.pagerduty.com/incidents"
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
                "total": "true"
            }
            
            incidents_response = requests.get(incidents_url, headers=headers, params=incidents_params, timeout=15)
            
            if incidents_response.status_code != 200:
                return f"<p style='color: #f56565;'>⚠️ PagerDuty API Error {incidents_response.status_code}: {incidents_response.reason}</p>"
            
            incidents_data = incidents_response.json()
            batch_incidents = incidents_data.get("incidents", [])
            all_incidents.extend(batch_incidents)
            
            # Check if there are more incidents to fetch
            more = incidents_data.get("more", False)
            offset += limit
            
            # Continue fetching all incidents without artificial limits
            if offset >= 10000:  # Safety limit to prevent truly infinite loops
                print(f"⚠️ PagerDuty Analytics: Reached safety limit of 10000 incidents")
                break
        
        incidents = all_incidents
        total_count = len(incidents)
        print(f"✅ PagerDuty Analytics: Fetched {total_count} total incidents")
        
        # Analyze incidents by status
        triggered = [i for i in incidents if i.get("status") == "triggered"]
        acknowledged = [i for i in incidents if i.get("status") == "acknowledged"]
        resolved = [i for i in incidents if i.get("status") == "resolved"]
        
        # Analyze by urgency
        high_urgency = [i for i in incidents if i.get("urgency") == "high"]
        low_urgency = [i for i in incidents if i.get("urgency") == "low"]
        
        # Count incidents by service
        service_counts = {}
        for incident in incidents:
            service_name = incident.get("service", {}).get("summary", "Unknown")
            service_counts[service_name] = service_counts.get(service_name, 0) + 1
        
        # Get top 10 services by incident count
        top_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Build HTML output
        html_output = f"""
        <h2 style='color: #10b981;'>📊 PagerDuty Analytics Dashboard - Last 30 Days</h2>
        
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 12px; margin-bottom: 20px; color: white;'>
            <h3 style='margin: 0 0 15px 0; color: white;'>📈 Overview Metrics</h3>
            <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;'>
                <div style='background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 32px; font-weight: bold;'>{total_count}</div>
                    <div style='font-size: 14px; opacity: 0.9;'>Total Incidents</div>
                </div>
                <div style='background: rgba(239, 68, 68, 0.2); padding: 15px; border-radius: 8px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 32px; font-weight: bold;'>{len(triggered)}</div>
                    <div style='font-size: 14px; opacity: 0.9;'>🔴 Triggered</div>
                </div>
                <div style='background: rgba(245, 158, 11, 0.2); padding: 15px; border-radius: 8px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 32px; font-weight: bold;'>{len(acknowledged)}</div>
                    <div style='font-size: 14px; opacity: 0.9;'>🟡 Acknowledged</div>
                </div>
                <div style='background: rgba(16, 185, 129, 0.2); padding: 15px; border-radius: 8px; backdrop-filter: blur(10px);'>
                    <div style='font-size: 32px; font-weight: bold;'>{len(resolved)}</div>
                    <div style='font-size: 14px; opacity: 0.9;'>🟢 Resolved</div>
                </div>
            </div>
        </div>
        
        <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;'>
            <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
                <h4 style='margin: 0 0 15px 0; color: #1f2937;'>📊 Incidents by Status</h4>
                <canvas id='status-chart' style='max-height: 300px;'></canvas>
            </div>
            
            <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
                <h4 style='margin: 0 0 15px 0; color: #1f2937;'>⚡ Incidents by Urgency</h4>
                <canvas id='urgency-chart' style='max-height: 300px;'></canvas>
            </div>
        </div>
        
        <div style='background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px;'>
            <h4 style='margin: 0 0 15px 0; color: #1f2937;'>🔝 Top 10 Services by Incident Count</h4>
            <canvas id='services-chart' style='max-height: 400px;'></canvas>
        </div>
        
        <script>
        (function() {{
            // Status Chart (Donut)
            const statusCtx = document.getElementById('status-chart');
            if (statusCtx) {{
                new Chart(statusCtx, {{
                    type: 'doughnut',
                    data: {{
                        labels: ['Triggered', 'Acknowledged', 'Resolved'],
                        datasets: [{{
                            data: [{len(triggered)}, {len(acknowledged)}, {len(resolved)}],
                            backgroundColor: ['#ef4444', '#f59e0b', '#10b981'],
                            borderWidth: 2,
                            borderColor: '#fff'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {{
                            legend: {{
                                position: 'bottom',
                                labels: {{
                                    padding: 15,
                                    font: {{ size: 12 }}
                                }}
                            }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(context) {{
                                        let label = context.label || '';
                                        let value = context.parsed || 0;
                                        let total = {total_count};
                                        let percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                        return label + ': ' + value + ' (' + percentage + '%)';
                                    }}
                                }}
                            }}
                        }}
                    }}
                }});
            }}
            
            // Urgency Chart (Donut)
            const urgencyCtx = document.getElementById('urgency-chart');
            if (urgencyCtx) {{
                new Chart(urgencyCtx, {{
                    type: 'doughnut',
                    data: {{
                        labels: ['High Urgency', 'Low Urgency'],
                        datasets: [{{
                            data: [{len(high_urgency)}, {len(low_urgency)}],
                            backgroundColor: ['#dc2626', '#fbbf24'],
                            borderWidth: 2,
                            borderColor: '#fff'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {{
                            legend: {{
                                position: 'bottom',
                                labels: {{
                                    padding: 15,
                                    font: {{ size: 12 }}
                                }}
                            }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(context) {{
                                        let label = context.label || '';
                                        let value = context.parsed || 0;
                                        let total = {total_count};
                                        let percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                        return label + ': ' + value + ' (' + percentage + '%)';
                                    }}
                                }}
                            }}
                        }}
                    }}
                }});
            }}
            
            // Services Chart (Horizontal Bar)
            const servicesCtx = document.getElementById('services-chart');
            if (servicesCtx) {{
                new Chart(servicesCtx, {{
                    type: 'bar',
                    data: {{
                        labels: {json.dumps([s[0][:30] + '...' if len(s[0]) > 30 else s[0] for s in top_services])},
                        datasets: [{{
                            label: 'Incident Count',
                            data: {json.dumps([s[1] for s in top_services])},
                            backgroundColor: 'rgba(99, 102, 241, 0.8)',
                            borderColor: 'rgba(99, 102, 241, 1)',
                            borderWidth: 1
                        }}]
                    }},
                    options: {{
                        indexAxis: 'y',
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{ display: false }},
                            tooltip: {{
                                callbacks: {{
                                    title: function(context) {{
                                        return {json.dumps([s[0] for s in top_services])}[context[0].dataIndex];
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            x: {{
                                beginAtZero: true,
                                ticks: {{ precision: 0 }}
                            }}
                        }}
                    }}
                }});
            }}
        }})();
        </script>
        
        <div style='background: #f3f4f6; padding: 15px; border-radius: 8px; margin-top: 20px;'>
            <p style='margin: 0; color: #6b7280; font-size: 13px;'>
                ℹ️ Data covers the last 30 days. Charts are generated using real-time data from PagerDuty API.
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
