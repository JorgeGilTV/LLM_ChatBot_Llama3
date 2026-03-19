"""
OneView API Client Examples
Demonstrates how to use the REST API endpoints
"""
import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000"


def print_section(title):
    """Print section separator"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_health_check():
    """Test the health check endpoint"""
    print_section("Health Check")
    
    response = requests.get(f"{BASE_URL}/api/health")
    data = response.json()
    
    print(f"Status: {data['status']}")
    print(f"Version: {data['version']}")
    print(f"Database: {data['database']['file_size_mb']} MB")
    print(f"Total Metrics: {data['database']['total_metrics']:,}")
    print(f"Total Snapshots: {data['database']['total_snapshots']:,}")


def get_current_status(environment=None):
    """Get current status for all or specific environment"""
    print_section(f"Current Status - {environment or 'All Environments'}")
    
    url = f"{BASE_URL}/api/status/current"
    params = {}
    if environment:
        params['environment'] = environment
    
    response = requests.get(url, params=params)
    data = response.json()
    
    print(f"Total Services: {data['total_services']}")
    print(f"Timestamp: {data['timestamp']}")
    print("\nServices by Status:")
    
    status_counts = {'healthy': 0, 'warning': 0, 'critical': 0}
    for service in data['services']:
        status_counts[service['status']] = status_counts.get(service['status'], 0) + 1
    
    print(f"  ✅ Healthy: {status_counts.get('healthy', 0)}")
    print(f"  ⚠️  Warning: {status_counts.get('warning', 0)}")
    print(f"  🚨 Critical: {status_counts.get('critical', 0)}")
    
    # Show critical services
    critical = [s for s in data['services'] if s['status'] == 'critical']
    if critical:
        print("\n🚨 Critical Services:")
        for svc in critical:
            print(f"   • {svc['service']} ({svc['environment']}): {svc['error_rate']:.2f}% errors")
    
    return data


def get_production_status():
    """Get detailed production status"""
    print_section("Production Environment Status")
    
    response = requests.get(f"{BASE_URL}/api/status/production")
    data = response.json()
    
    summary = data['summary']
    print(f"Environment: {data['environment']}")
    print(f"Total: {summary['total_services']}")
    print(f"  ✅ Healthy: {summary['healthy']}")
    print(f"  ⚠️  Warning: {summary['warning']}")
    print(f"  🚨 Critical: {summary['critical']}")
    
    return data


def get_service_history(service_name, environment='production', hours=24):
    """Get historical data for a specific service"""
    print_section(f"Service History: {service_name}")
    
    response = requests.get(
        f"{BASE_URL}/api/history/service/{service_name}",
        params={'environment': environment, 'hours': hours}
    )
    data = response.json()
    
    print(f"Service: {data['service']}")
    print(f"Environment: {data['environment']}")
    print(f"Time Range: {data['hours']} hours")
    print(f"Data Points: {data['data_points']}")
    
    if data['history']:
        latest = data['history'][-1]
        print(f"\nLatest Metrics:")
        print(f"  Status: {latest['status']}")
        print(f"  Requests: {latest['requests']:,}")
        print(f"  Error Rate: {latest['error_rate']:.2f}%")
        if latest.get('p95_latency'):
            print(f"  P95 Latency: {latest['p95_latency']:.1f}ms")
    
    return data


def get_service_trends(service_name, environment='production', hours=24):
    """Get trend analysis for a service"""
    print_section(f"Service Trends: {service_name}")
    
    response = requests.get(
        f"{BASE_URL}/api/trends/service/{service_name}",
        params={'environment': environment, 'hours': hours}
    )
    data = response.json()
    
    trends = data['trends']
    print(f"Service: {trends['service']}")
    print(f"Environment: {trends['environment']}")
    print(f"Trend: {trends['trend'].upper()}")
    print(f"Data Points: {trends['data_points']}")
    print(f"\nAverage Error Rate: {trends['avg_error_rate']:.3f}%")
    print(f"Max Error Rate: {trends['max_error_rate']:.3f}%")
    print(f"Min Error Rate: {trends['min_error_rate']:.3f}%")
    print(f"Average Requests: {trends['avg_requests']:,.0f}")
    
    # Interpret trend
    if trends['trend'] == 'degrading':
        print("\n⚠️  WARNING: Service is degrading!")
    elif trends['trend'] == 'improving':
        print("\n✅ Service is improving!")
    else:
        print("\n➡️  Service is stable")
    
    return data


def get_dashboard_history(environment='production', hours=24):
    """Get dashboard history"""
    print_section(f"Dashboard History - {environment}")
    
    response = requests.get(
        f"{BASE_URL}/api/history/dashboard",
        params={'environment': environment, 'hours': hours}
    )
    data = response.json()
    
    print(f"Environment: {data['environment']}")
    print(f"Time Range: {data['hours']} hours")
    print(f"Data Points: {data['data_points']}")
    
    if data['history']:
        latest = data['history'][-1]
        print(f"\nLatest Snapshot:")
        print(f"  Total Services: {latest['total_services']}")
        print(f"  Healthy: {latest['healthy_count']}")
        print(f"  Warning: {latest['warning_count']}")
        print(f"  Critical: {latest['critical_count']}")
        print(f"  Overall Error Rate: {latest['overall_error_rate']:.3f}%")
        print(f"  PD Triggered: {latest['pd_triggered']}")
    
    return data


def get_critical_history(hours=24):
    """Get history of critical incidents"""
    print_section(f"Critical Incidents (Last {hours} hours)")
    
    response = requests.get(
        f"{BASE_URL}/api/critical/history",
        params={'hours': hours}
    )
    data = response.json()
    
    print(f"Total Incidents: {data['total_incidents']}")
    
    if data['incidents']:
        print("\nRecent Critical Incidents:")
        for incident in data['incidents'][:10]:  # Show first 10
            print(f"  • {incident['timestamp']}: {incident['service']} ({incident['environment']})")
            print(f"    Error Rate: {incident['error_rate']:.2f}%")
            if incident['pd_incident']:
                print(f"    🚨 PagerDuty Alert Active")
    
    return data


def monitor_production_real_time():
    """Example: Real-time monitoring script"""
    print_section("Real-Time Production Monitoring")
    
    response = requests.get(f"{BASE_URL}/api/status/production")
    data = response.json()
    
    summary = data['summary']
    
    # Check for critical services
    if summary['critical'] > 0:
        print(f"🚨 ALERT: {summary['critical']} critical services detected!")
        
        critical_services = [s for s in data['services'] if s['status'] == 'critical']
        for svc in critical_services:
            print(f"\n   Service: {svc['service']}")
            print(f"   Error Rate: {svc['error_rate']:.2f}%")
            print(f"   Requests: {svc['requests']:,}")
            if svc.get('pd_incident'):
                print(f"   🚨 PagerDuty Alert Active")
    else:
        print("✅ All production services healthy!")
    
    return data


if __name__ == '__main__':
    try:
        # Run all tests
        test_health_check()
        get_current_status()
        get_production_status()
        
        # Note: These will only have data after the dashboard has been accessed
        # and metrics have been saved
        print("\n" + "="*60)
        print("  NOTE: Historical endpoints require data collection")
        print("  Visit the status monitor dashboard to start collecting data")
        print("="*60)
        
        # Try historical endpoints (may be empty initially)
        get_dashboard_history(environment='production', hours=24)
        get_critical_history(hours=24)
        
        # Example: monitor production
        monitor_production_real_time()
        
        print("\n" + "="*60)
        print("  ✅ All tests completed successfully!")
        print("="*60)
        
    except requests.exceptions.ConnectionError:
        print("\n❌ ERROR: Could not connect to the server")
        print(f"   Make sure the application is running at {BASE_URL}")
        print(f"   Start with: docker run -p 5000:5000 oneview-goc-ai:latest")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
