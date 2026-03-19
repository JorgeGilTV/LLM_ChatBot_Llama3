#!/usr/bin/env python3
"""
Debug script to test different Datadog latency metric patterns
Helps identify which metric name works for a specific service
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

def test_metric_pattern(service_name, environment, metric_query, pattern_name):
    """Test a specific metric pattern and report results"""
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key
    }
    
    current_time = int(time.time())
    from_time = current_time - (4 * 3600)  # 4 hours
    
    params = {
        "from": from_time,
        "to": current_time,
        "query": metric_query
    }
    
    print(f"\n{'='*80}")
    print(f"🧪 Testing: {pattern_name}")
    print(f"📝 Query: {metric_query}")
    print('='*80)
    
    try:
        response = requests.get(
            f"https://{dd_site}/api/v1/query",
            headers=headers,
            params=params,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            series = data.get('series', [])
            
            print(f"Series Count: {len(series)}")
            
            if series and len(series) > 0:
                pointlist = series[0].get('pointlist', [])
                print(f"Points Count: {len(pointlist)}")
                
                if pointlist:
                    # Show first few points
                    print(f"\nFirst 5 points:")
                    for i, point in enumerate(pointlist[:5]):
                        timestamp, value = point
                        print(f"  {i+1}. Timestamp: {timestamp}, Value: {value}")
                    
                    # Calculate statistics
                    valid_values = [p[1] for p in pointlist if p[1] is not None and p[1] > 0]
                    
                    if valid_values:
                        avg_value = sum(valid_values) / len(valid_values)
                        min_value = min(valid_values)
                        max_value = max(valid_values)
                        
                        print(f"\n📊 Statistics:")
                        print(f"   Valid Points: {len(valid_values)}/{len(pointlist)}")
                        print(f"   Average: {avg_value:.6f}")
                        print(f"   Min: {min_value:.6f}")
                        print(f"   Max: {max_value:.6f}")
                        
                        # Try to detect if it's seconds or milliseconds
                        if avg_value < 1:
                            print(f"   🔍 Likely in SECONDS (avg < 1)")
                            print(f"   ➡️  Convert to ms: {avg_value * 1000:.2f}ms")
                        else:
                            print(f"   🔍 Likely in MILLISECONDS (avg >= 1)")
                            print(f"   ➡️  Already in ms: {avg_value:.2f}ms")
                        
                        return True, avg_value
                    else:
                        print(f"❌ All values are None or 0")
                        return False, None
                else:
                    print(f"❌ No points in pointlist")
                    return False, None
            else:
                print(f"❌ No series data returned")
                
                # Print response for debugging
                print(f"\nFull response:")
                print(data)
                return False, None
        else:
            print(f"❌ API Error: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False, None
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def main():
    service = "backend-hmsmatter"  # Full service name
    environment = "goldendev"
    
    print(f"\n{'#'*80}")
    print(f"# Testing Datadog Latency Metrics for: {service} ({environment})")
    print(f"{'#'*80}\n")
    
    # Test different metric patterns
    patterns_to_test = [
        # Pattern 1: duration.by.service (most common)
        (
            f"avg:trace.servlet.request.duration.by.service.95p{{service:{service},env:{environment}}}",
            "duration.by.service.95p"
        ),
        # Pattern 2: duration.by.resource_service
        (
            f"avg:trace.servlet.request.duration.by.resource_service.95p{{service:{service},env:{environment}}}",
            "duration.by.resource_service.95p"
        ),
        # Pattern 3: p95 aggregation on duration
        (
            f"p95:trace.servlet.request.duration{{service:{service},env:{environment}}}",
            "p95:duration"
        ),
        # Pattern 4: Just duration without percentile in name
        (
            f"avg:trace.servlet.request.duration{{service:{service},env:{environment}}}",
            "avg:duration (no percentile)"
        ),
        # Pattern 5: Try without .servlet (generic trace)
        (
            f"avg:trace.request.duration.by.service.95p{{service:{service},env:{environment}}}",
            "trace.request (no servlet)"
        ),
    ]
    
    results = []
    
    for query, name in patterns_to_test:
        success, value = test_metric_pattern(service, environment, query, name)
        results.append((name, success, value))
        time.sleep(0.5)  # Be nice to the API
    
    # Summary
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print('='*80)
    
    working_patterns = [r for r in results if r[1]]
    
    if working_patterns:
        print(f"\n✅ Working patterns ({len(working_patterns)}):")
        for name, success, value in working_patterns:
            value_display = f"{value:.6f}" if value else "N/A"
            print(f"   • {name}: {value_display}")
    else:
        print(f"\n❌ No patterns returned data!")
        print(f"\nPossible reasons:")
        print(f"   1. Service name mismatch (check exact name in Datadog APM)")
        print(f"   2. Environment name mismatch (goldendev vs golden-dev)")
        print(f"   3. No traffic in the selected time range")
        print(f"   4. Service doesn't use trace.servlet.* metrics")
        print(f"\n💡 Suggestions:")
        print(f"   - Check the service in Datadog APM UI")
        print(f"   - Look at the actual metric names in the service page")
        print(f"   - Try a different time range")
        print(f"   - Try removing 'backend-' prefix if present")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        service = sys.argv[1]
        environment = sys.argv[2] if len(sys.argv) > 2 else "goldendev"
        print(f"Testing custom service: {service} ({environment})")
    
    main()
