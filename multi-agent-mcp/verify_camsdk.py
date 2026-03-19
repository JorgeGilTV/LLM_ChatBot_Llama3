#!/usr/bin/env python3
"""
Quick verification of camsdk-webserver metrics
"""
import sys
sys.path.insert(0, '/Users/jgilmacias.c/Documents/GenAI/LLM_ChatBot_Llama3/multi-agent-mcp')

import os
import time
from dotenv import load_dotenv

load_dotenv()

# Import the function
from tools.status_monitor import get_service_health_status

def verify_service():
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        print("❌ Missing credentials")
        return
    
    current_time = int(time.time())
    from_time = current_time - (4 * 3600)
    
    services_to_check = [
        ("backend-camsdk-webserver", "goldenqa"),
        ("camsdk-webserver", "goldenqa"),
        ("backend-camsdk-webserver", "goldendev"),
    ]
    
    print(f"\n{'='*80}")
    print("🔍 Checking camsdk-webserver metrics")
    print('='*80)
    
    for service, env in services_to_check:
        print(f"\n📊 Service: {service} | Environment: {env}")
        print("-" * 80)
        
        result = get_service_health_status(
            service, env,
            dd_api_key, dd_app_key, dd_site,
            from_time, current_time,
            enable_extended_metrics=True
        )
        
        print(f"Status: {result['status']}")
        print(f"Requests: {result['requests']:,}")
        print(f"Errors: {result['errors']:,}")
        print(f"Error Rate: {result['error_rate']:.2f}%")
        
        if result['p95_latency']:
            print(f"P95 Latency: {result['p95_latency']:.2f}ms")
            
            # Check if this looks suspicious
            if result['p95_latency'] > 10000:
                print(f"⚠️  WARNING: Very high latency (>10s)! This might be a conversion issue.")
        else:
            print(f"P95 Latency: No data")
        
        if result['p99_latency']:
            print(f"P99 Latency: {result['p99_latency']:.2f}ms")

if __name__ == '__main__':
    verify_service()
