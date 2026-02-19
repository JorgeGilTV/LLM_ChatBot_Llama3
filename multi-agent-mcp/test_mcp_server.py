#!/usr/bin/env python3
"""
Test script for OneView GOC AI MCP Server
Demonstrates how to interact with the MCP server endpoints
"""

import requests
import json

BASE_URL = "http://localhost:8080"

def test_mcp_info():
    """Test the MCP info endpoint"""
    print("=" * 60)
    print("Testing MCP Server Info Endpoint")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/mcp/info")
        response.raise_for_status()
        
        data = response.json()
        
        print(f"\n✅ Server Name: {data['name']}")
        print(f"✅ Version: {data['version']}")
        print(f"✅ Protocol: {data['protocol']}")
        print(f"✅ Transport: {data['transport']}")
        print(f"✅ Total Tools: {data['total_tools']}")
        
        print("\n📋 Available Tools:")
        print("-" * 60)
        for tool in data['tools']:
            print(f"  • {tool['name']:<25} - {tool['description']}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_tool_execution():
    """Example: Test tool execution through the regular API"""
    print("\n" + "=" * 60)
    print("Testing Tool Execution (Regular API)")
    print("=" * 60)
    
    # Test the arlo status endpoint as an example
    try:
        response = requests.get(f"{BASE_URL}/api/arlo-status")
        response.raise_for_status()
        
        data = response.json()
        
        print(f"\n✅ Arlo Status: {data.get('arlo_status', 'N/A')}")
        print(f"✅ Services: {len(data.get('services', []))} found")
        
        if data.get('services'):
            print("\n📊 Service Status:")
            print("-" * 60)
            for service in data['services'][:5]:  # Show first 5
                status = service.get('status', 'Unknown')
                emoji = '🟢' if status.lower() == 'all good' else '🔴'
                print(f"  {emoji} {service.get('name', 'Unknown')}: {status}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_deployments():
    """Test deployments endpoint"""
    print("\n" + "=" * 60)
    print("Testing Deployments Endpoint")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/deployments/upcoming")
        response.raise_for_status()
        
        data = response.json()
        
        print(f"\n✅ Total Deployments: {len(data.get('deployments', []))}")
        
        if data.get('deployments'):
            print("\n📅 Next Deployments:")
            print("-" * 60)
            for deploy in data['deployments'][:5]:  # Show first 5
                print(f"  • {deploy.get('date')} {deploy.get('time')} - {deploy.get('service')}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def main():
    """Run all tests"""
    print("\n🧪 OneView GOC AI - MCP Server Test Suite\n")
    
    tests = [
        ("MCP Info", test_mcp_info),
        ("Tool Execution", test_tool_execution),
        ("Deployments", test_deployments),
    ]
    
    results = []
    for test_name, test_func in tests:
        result = test_func()
        results.append((test_name, result))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! MCP Server is ready.")
    else:
        print("\n⚠️  Some tests failed. Check the server logs.")


if __name__ == "__main__":
    main()
