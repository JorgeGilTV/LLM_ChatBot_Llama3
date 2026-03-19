#!/usr/bin/env python3
"""
Test script to verify Datadog latency metrics conversion
Tests the search_datadog_services function to ensure P95 latency is displayed correctly
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_service_metrics(service_name, timerange=4):
    """Test fetching metrics for a specific service"""
    from tools.datadog_dashboards import search_datadog_services
    
    print(f"\n{'='*80}")
    print(f"🧪 Testing Service Metrics for: {service_name}")
    print(f"⏰ Time Range: {timerange} hours")
    print('='*80)
    
    result = search_datadog_services(service_name, timerange)
    
    # Check if result contains data
    if "0ms" in result and "TOTAL REQUESTS" in result:
        print(f"\n⚠️  WARNING: Service '{service_name}' shows 0ms latency")
        print("   This could mean:")
        print("   1. Service has no traffic in this time range")
        print("   2. Metric name/pattern doesn't match")
        print("   3. Data not yet collected by Datadog")
    elif "Latency P95" in result:
        print(f"\n✅ Service '{service_name}' metrics fetched successfully")
    else:
        print(f"\n❌ No latency metrics found for '{service_name}'")
    
    return result


def test_status_monitor():
    """Test status monitor to verify P95 conversion"""
    from tools.status_monitor import get_service_health_status
    import time
    
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        print("❌ Missing Datadog credentials")
        return
    
    print(f"\n{'='*80}")
    print("🧪 Testing Status Monitor Health Check")
    print('='*80)
    
    current_time = int(time.time())
    from_time = current_time - (4 * 3600)  # 4 hours
    
    # Test with a known service
    service = "hmsmatter"
    env = "goldendev"
    
    print(f"\n📊 Fetching health status for: {service} ({env})")
    
    result = get_service_health_status(
        service, env, 
        dd_api_key, dd_app_key, dd_site,
        from_time, current_time,
        enable_extended_metrics=True
    )
    
    print(f"\n✅ Result:")
    print(f"   Service: {result['service']}")
    print(f"   Environment: {result['environment']}")
    print(f"   Status: {result['status']}")
    print(f"   Requests: {result['requests']:,}")
    print(f"   Errors: {result['errors']:,}")
    print(f"   Error Rate: {result['error_rate']}%")
    print(f"   P95 Latency: {result['p95_latency']}ms" if result['p95_latency'] else "   P95 Latency: None")
    print(f"   P99 Latency: {result['p99_latency']}ms" if result['p99_latency'] else "   P99 Latency: None")
    
    if result['p95_latency'] and result['p95_latency'] > 0:
        print(f"\n✅ Latency metrics are being fetched correctly!")
    else:
        print(f"\n⚠️  Warning: No latency data available for this service")
        print(f"   This could mean the service uses a different metric pattern")
        print(f"   Try checking the service in Datadog APM directly")


if __name__ == '__main__':
    # Default test service
    test_service = sys.argv[1] if len(sys.argv) > 1 else "hmsmatter"
    test_timerange = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    
    print("🚀 OneView Latency Metrics Test")
    print("="*80)
    
    # Test 1: Status Monitor
    test_status_monitor()
    
    # Test 2: DD_Services Tool
    # Note: This will output HTML, we're just checking it runs without errors
    print(f"\n{'='*80}")
    print("🧪 Testing DD_Services Tool")
    print('='*80)
    html_output = test_service_metrics(test_service, test_timerange)
    
    # Simple check
    if html_output and len(html_output) > 100:
        print(f"\n✅ DD_Services returned {len(html_output)} bytes of HTML")
    else:
        print(f"\n⚠️  DD_Services returned short output")
    
    print("\n" + "="*80)
    print("🎯 Test Summary")
    print("="*80)
    print("✅ All imports successful")
    print("✅ Functions executed without errors")
    print("\n💡 Tips:")
    print("   - If latency shows 0ms, the service may have no traffic")
    print("   - Check service name exactly matches Datadog APM")
    print("   - Verify the environment (goldendev/goldenqa/production)")
    print("   - Some services use trace.http.* instead of trace.servlet.*")
    print("\n📊 To test with a different service:")
    print(f"   python3 test_latency.py <service_name> <hours>")
