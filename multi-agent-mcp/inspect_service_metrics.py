#!/usr/bin/env python3
"""
Inspect all available metrics for a specific service in Datadog
This helps identify the correct metric names for latency
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def search_metrics_for_service(service_name):
    """Search for all metrics available for a service"""
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        print("❌ Missing Datadog credentials")
        return
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key
    }
    
    print(f"\n{'='*80}")
    print(f"🔍 Searching metrics for service: {service_name}")
    print('='*80)
    
    # Search for metrics with this service name
    search_url = f"https://{dd_site}/api/v1/metrics"
    
    try:
        # Use the search endpoint
        response = requests.get(
            f"https://{dd_site}/api/v1/search",
            headers=headers,
            params={'q': f"metrics:trace.*.{service_name}"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', {})
            metrics = results.get('metrics', [])
            
            print(f"\n📊 Found {len(metrics)} metrics:")
            
            # Filter for duration/latency related metrics
            duration_metrics = [m for m in metrics if 'duration' in m.lower() or 'latency' in m.lower()]
            
            if duration_metrics:
                print(f"\n⏱️  Duration/Latency metrics ({len(duration_metrics)}):")
                for metric in duration_metrics[:20]:
                    print(f"   • {metric}")
            else:
                print(f"\n⚠️  No duration/latency metrics found")
                print(f"\nAll metrics:")
                for metric in metrics[:30]:
                    print(f"   • {metric}")
        else:
            print(f"❌ Search API error: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def list_active_metrics_for_tag(service_tag):
    """List active metrics using tag search"""
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key
    }
    
    print(f"\n{'='*80}")
    print(f"🏷️  Listing active metrics with tag: service:{service_tag}")
    print('='*80)
    
    try:
        # Query metrics endpoint with host filter
        response = requests.get(
            f"https://{dd_site}/api/v1/metrics",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            all_metrics = data.get('metrics', [])
            
            # Filter for trace metrics
            trace_metrics = [m for m in all_metrics if m.startswith('trace.')]
            servlet_metrics = [m for m in trace_metrics if 'servlet' in m]
            duration_metrics = [m for m in servlet_metrics if 'duration' in m]
            
            print(f"\n📊 Total trace.* metrics: {len(trace_metrics)}")
            print(f"📊 Total trace.servlet.* metrics: {len(servlet_metrics)}")
            print(f"📊 Total trace.servlet.*duration* metrics: {len(duration_metrics)}")
            
            if duration_metrics:
                print(f"\n⏱️  Available duration metrics:")
                for metric in sorted(duration_metrics):
                    print(f"   • {metric}")
            
            # Look for percentile metrics specifically
            percentile_metrics = [m for m in servlet_metrics if any(p in m for p in ['50p', '75p', '90p', '95p', '99p'])]
            if percentile_metrics:
                print(f"\n🎯 Percentile metrics ({len(percentile_metrics)}):")
                for metric in sorted(percentile_metrics):
                    print(f"   • {metric}")
        else:
            print(f"❌ Metrics API error: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")


def test_actual_service():
    """Test with the actual service that's showing 0ms"""
    service_variants = [
        "hmsmatter",
        "backend-hmsmatter"
    ]
    
    environment = "goldendev"
    
    for service in service_variants:
        print(f"\n{'#'*80}")
        print(f"# Testing service variant: {service}")
        print(f"{'#'*80}")
        
        # Pattern 1: Standard by.service
        test_metric_pattern(
            service, environment,
            f"avg:trace.servlet.request.duration.by.service.95p{{service:{service},env:{environment}}}",
            "Standard: duration.by.service.95p"
        )
        
        # Pattern 2: by.resource_service
        test_metric_pattern(
            service, environment,
            f"avg:trace.servlet.request.duration.by.resource_service.95p{{service:{service},env:{environment}}}",
            "Alternative: duration.by.resource_service.95p"
        )
        
        # Pattern 3: Just duration without percentile aggregator
        test_metric_pattern(
            service, environment,
            f"avg:trace.servlet.request.duration{{service:{service},env:{environment}}}",
            "Generic: avg duration (all percentiles)"
        )
        
        # Pattern 4: p95 aggregation
        test_metric_pattern(
            service, environment,
            f"p95:trace.servlet.request.duration{{service:{service},env:{environment}}}",
            "Aggregation: p95:duration"
        )


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        service = sys.argv[1]
        env = sys.argv[2] if len(sys.argv) > 2 else "goldendev"
        
        print(f"🧪 Testing custom service: {service} ({env})")
        
        # Test this specific service
        test_metric_pattern(
            service, env,
            f"avg:trace.servlet.request.duration.by.service.95p{{service:{service},env:{env}}}",
            "Standard Pattern"
        )
    else:
        # Run full test suite
        print("🚀 Running full latency metrics diagnostic")
        
        # First, list all available metrics
        list_active_metrics_for_tag("hmsmatter")
        
        # Then test specific service
        test_actual_service()
    
    print(f"\n{'='*80}")
    print("🎯 Diagnostic Complete")
    print('='*80)
    print("\n💡 Next steps:")
    print("   1. Check which pattern returned data")
    print("   2. Update the code to use the working pattern")
    print("   3. Verify if values are in seconds or milliseconds")
    print("   4. Test with the status monitor dashboard")
