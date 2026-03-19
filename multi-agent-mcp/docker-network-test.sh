#!/bin/bash

echo "🔍 OneView GOC AI - Network Diagnostics"
echo "========================================"
echo ""

# MCP Server Configuration
MCP_HOST="internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com"
MCP_PORT="8080"
MCP_URL="http://${MCP_HOST}:${MCP_PORT}"

echo "📡 Testing MCP Server Connectivity..."
echo "Target: ${MCP_URL}"
echo ""

# Test 1: DNS Resolution
echo "1️⃣ DNS Resolution Test:"
if nslookup ${MCP_HOST} > /dev/null 2>&1; then
    echo "   ✅ DNS resolves successfully"
    IP=$(nslookup ${MCP_HOST} | grep -A1 "Name:" | grep "Address:" | awk '{print $2}' | head -1)
    echo "   📍 Resolved IP: ${IP}"
else
    echo "   ❌ DNS resolution failed"
    echo "   💡 Solution: VM needs access to AWS internal DNS (VPC DNS server)"
    echo "      - Check /etc/resolv.conf"
    echo "      - Ensure VM is using VPC DNS (usually 10.x.0.2 or similar)"
fi
echo ""

# Test 2: Network Connectivity (ping)
echo "2️⃣ Network Connectivity Test (ICMP):"
if ping -c 3 ${MCP_HOST} > /dev/null 2>&1; then
    echo "   ✅ Host is reachable via ICMP"
else
    echo "   ⚠️  ICMP blocked (common for ALBs, not critical)"
fi
echo ""

# Test 3: Port Connectivity
echo "3️⃣ Port ${MCP_PORT} Connectivity Test:"
if timeout 5 bash -c "cat < /dev/null > /dev/tcp/${MCP_HOST}/${MCP_PORT}" 2>/dev/null; then
    echo "   ✅ Port ${MCP_PORT} is open and accessible"
else
    echo "   ❌ Cannot connect to port ${MCP_PORT}"
    echo "   💡 Possible issues:"
    echo "      - Security Group doesn't allow traffic from this VM"
    echo "      - Network ACLs blocking traffic"
    echo "      - Route table issue"
    echo "      - ALB listener not configured"
fi
echo ""

# Test 4: HTTP Request
echo "4️⃣ HTTP Request Test:"
if command -v curl &> /dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "${MCP_URL}/sse" 2>/dev/null)
    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 400 ] || [ "$HTTP_CODE" -eq 404 ]; then
        echo "   ✅ MCP Server is responding (HTTP ${HTTP_CODE})"
    else
        echo "   ❌ MCP Server not responding (HTTP ${HTTP_CODE})"
    fi
else
    echo "   ⚠️  curl not installed, skipping HTTP test"
fi
echo ""

# Test 5: Check AWS metadata (if running in EC2)
echo "5️⃣ AWS Environment Check:"
if curl -s --connect-timeout 2 http://169.254.169.254/latest/meta-data/instance-id > /dev/null 2>&1; then
    INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
    AZ=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)
    LOCAL_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
    VPC_ID=$(curl -s http://169.254.169.254/latest/meta-data/network/interfaces/macs/$(curl -s http://169.254.169.254/latest/meta-data/mac)/vpc-id 2>/dev/null)
    
    echo "   ✅ Running in AWS EC2"
    echo "   📍 Instance ID: ${INSTANCE_ID}"
    echo "   📍 Availability Zone: ${AZ}"
    echo "   📍 Local IP: ${LOCAL_IP}"
    echo "   📍 VPC ID: ${VPC_ID}"
else
    echo "   ⚠️  Not running in EC2 or metadata service unavailable"
    echo "   💡 If this is a VM in AWS, check if it has proper IAM role"
fi
echo ""

# Test 6: DNS Server Check
echo "6️⃣ DNS Configuration:"
if [ -f /etc/resolv.conf ]; then
    echo "   DNS Servers:"
    grep "nameserver" /etc/resolv.conf | while read line; do
        echo "   📍 $line"
    done
    
    # Check if using VPC DNS
    VPC_DNS=$(grep "nameserver" /etc/resolv.conf | grep -E "10\.|172\." | head -1)
    if [ -n "$VPC_DNS" ]; then
        echo "   ✅ Using VPC DNS (private IP range)"
    else
        echo "   ⚠️  Not using VPC DNS - may not resolve internal AWS names"
        echo "   💡 Expected: nameserver should be VPC CIDR +2 (e.g., 10.0.0.2)"
    fi
fi
echo ""

# Summary
echo "========================================"
echo "📋 SUMMARY & RECOMMENDATIONS"
echo "========================================"
echo ""

# Check if all critical tests passed
DNS_OK=$(nslookup ${MCP_HOST} > /dev/null 2>&1 && echo "yes" || echo "no")
PORT_OK=$(timeout 5 bash -c "cat < /dev/null > /dev/tcp/${MCP_HOST}/${MCP_PORT}" 2>/dev/null && echo "yes" || echo "no")

if [ "$DNS_OK" = "yes" ] && [ "$PORT_OK" = "yes" ]; then
    echo "✅ All critical tests passed!"
    echo "   The VM can reach the MCP server."
    echo ""
    echo "If the app still shows VPN error:"
    echo "  1. Check Docker container logs: docker logs goc-ai"
    echo "  2. Verify credentials are set correctly"
    echo "  3. Check if ALB requires authentication token"
else
    echo "❌ Connection issues detected:"
    echo ""
    
    if [ "$DNS_OK" = "no" ]; then
        echo "🔧 DNS Issue:"
        echo "   - Edit /etc/resolv.conf"
        echo "   - Add: nameserver <VPC_DNS_IP>"
        echo "   - Usually: 10.0.0.2 or 172.31.0.2"
        echo "   - Or use systemd-resolved if available"
        echo ""
    fi
    
    if [ "$PORT_OK" = "no" ]; then
        echo "🔧 Network Issue:"
        echo "   - Check Security Group of ALB"
        echo "   - Ensure it allows inbound on port ${MCP_PORT} from VM's security group"
        echo "   - Check Network ACLs"
        echo "   - Verify route tables"
        echo "   - AWS CLI command to check SG:"
        echo "     aws ec2 describe-security-groups --group-ids <sg-id>"
        echo ""
    fi
fi

echo "📚 For more help, see:"
echo "   - DOCKER_DEPLOYMENT.md"
echo "   - docker logs goc-ai (for app logs)"
echo ""
