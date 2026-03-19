#!/bin/bash
# API Testing Script
# Make sure the application is running before executing these tests
# Usage: chmod +x test_api.sh && ./test_api.sh

BASE_URL="http://localhost:5000"

echo "🧪 Testing OneView REST API Endpoints"
echo "======================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test Health Check
echo -e "${BLUE}1. Health Check${NC}"
curl -s "${BASE_URL}/api/health" | python3 -m json.tool
echo ""
echo ""

# Test Current Status (All)
echo -e "${BLUE}2. Current Status (All Services)${NC}"
curl -s "${BASE_URL}/api/status/current" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

# Test Current Status (Production only)
echo -e "${BLUE}3. Current Status (Production Only)${NC}"
curl -s "${BASE_URL}/api/status/current?environment=production" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

# Test Status by Environment
echo -e "${BLUE}4. Status by Environment (Production)${NC}"
curl -s "${BASE_URL}/api/status/production" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

# Test Dashboard History
echo -e "${BLUE}5. Dashboard History (Last 24 hours)${NC}"
curl -s "${BASE_URL}/api/history/dashboard?environment=production&hours=24" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

# Test Service History (example with arlo-api)
echo -e "${BLUE}6. Service History (arlo-api, 24 hours)${NC}"
curl -s "${BASE_URL}/api/history/service/arlo-api?environment=production&hours=24" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

# Test Service Trends
echo -e "${BLUE}7. Service Trends (arlo-api)${NC}"
curl -s "${BASE_URL}/api/trends/service/arlo-api?environment=production&hours=24" | python3 -m json.tool
echo ""
echo ""

# Test Critical History
echo -e "${BLUE}8. Critical Services History (Last 24 hours)${NC}"
curl -s "${BASE_URL}/api/critical/history?hours=24" | python3 -m json.tool | head -50
echo "... (truncated)"
echo ""
echo ""

echo -e "${GREEN}✅ All API tests completed!${NC}"
echo ""
echo "📖 For full API documentation, see API_DOCUMENTATION.md"
