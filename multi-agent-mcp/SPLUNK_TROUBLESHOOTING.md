# 🔧 Splunk Connection Troubleshooting Guide

## 🚨 Problem: Connection Timeouts

If you're seeing errors like:
```
Connection to arlo.splunkcloud.com timed out. (connect timeout=10)
Max retries exceeded with url: /services/search/jobs/export
```

## 🔍 Root Causes

### 1. **IP Not Whitelisted** (Most Common - 90% of cases)
Splunk Cloud requires IP whitelisting for API access on port 8089.

**Solution:**
1. Check your public IP:
   ```bash
   curl https://api.ipify.org
   ```
   Or visit: https://whatismyipaddress.com

2. Contact your Splunk administrator with:
   - Your IP address or CIDR range
   - Request to whitelist for API access (port 8089)

3. **For Verisure/Arlo:**
   - Email: splunk-admin@verisure.com (or your internal Splunk team)
   - Provide your IP: `XXX.XXX.XXX.XXX`
   - Mention you need API access to `arlo.splunkcloud.com:8089`

### 2. **VPN Required**
Your organization may require VPN connection to access Splunk Cloud.

**Solution:**
- Connect to your corporate VPN
- Retry the Splunk query
- Verify VPN is routing to Splunk endpoints

### 3. **Firewall Blocking Port 8089**
Corporate firewall or ISP may block the Splunk API port.

**Solution:**
- Check with your network team
- Verify outbound connections to `*.splunkcloud.com:8089` are allowed
- Test connectivity:
  ```bash
  telnet arlo.splunkcloud.com 8089
  # or
  nc -zv arlo.splunkcloud.com 8089
  ```

### 4. **Invalid or Expired Token**
SPLUNK_TOKEN may be incorrect or expired.

**Solution:**
1. Verify token in `.env` file:
   ```bash
   cat .env | grep SPLUNK_TOKEN
   ```

2. Get a new token:
   - Log into Splunk Cloud: https://arlo.splunkcloud.com
   - Go to: Settings → Tokens → Create New Token
   - Update `.env` with new token
   - Restart application

### 5. **Wrong Splunk Host**
Incorrect hostname in configuration.

**Solution:**
- Verify in `.env`:
  ```bash
  SPLUNK_HOST=arlo.splunkcloud.com
  ```
- Should NOT include `https://` or port
- Should NOT include path like `/en-US/app/...`

## ✅ Quick Diagnosis Checklist

Run these tests in order:

### Test 1: Check Public IP
```bash
curl https://api.ipify.org
```
**Note this IP** - you'll need it for whitelisting.

### Test 2: Test DNS Resolution
```bash
nslookup arlo.splunkcloud.com
```
Should return valid IP address(es).

### Test 3: Test Port Connectivity
```bash
telnet arlo.splunkcloud.com 8089
# or
nc -zv arlo.splunkcloud.com 8089
```
**If this times out → IP not whitelisted or firewall blocking.**

### Test 4: Verify Token
```bash
echo $SPLUNK_TOKEN
# Should show a long alphanumeric string
```

### Test 5: Try Splunk Web UI
Open browser: https://arlo.splunkcloud.com
- If accessible → VPN/network is OK, likely just API whitelisting needed
- If not accessible → VPN or network issue

## 🛠️ Solutions Applied

### In GOC_AgenticAI v2.0:

1. **Increased Timeouts:**
   - Connect timeout: 10s → 30s
   - Read timeout: 120s → 180s

2. **Better Error Messages:**
   - Specific error types detected
   - Helpful troubleshooting guide shown in UI
   - Direct links to resolution steps

3. **Improved Error Handling:**
   - ConnectTimeout → IP whitelist warning
   - Timeout → Query complexity warning
   - ConnectionError → Firewall/VPN warning

## 📋 Request Template for Splunk Admin

Use this template when requesting IP whitelisting:

```
Subject: Request to Whitelist IP for Splunk API Access

Hi Splunk Admin Team,

I need API access to Splunk Cloud for the GOC_AgenticAI application.

Details:
- Splunk Instance: arlo.splunkcloud.com
- Port Required: 8089 (REST API)
- My Public IP: [YOUR_IP_FROM_STEP_1]
- Application: GOC_AgenticAI - Operations Dashboard
- Purpose: Automated querying of P0 Streaming dashboards

Please whitelist this IP for REST API access.

Alternative: If you prefer CIDR range, our office range is:
[YOUR_CIDR_RANGE, e.g., 189.128.129.0/24]

Thank you!
```

## 🔄 Restart Application After Changes

After fixing the issue:

```bash
# If running locally
lsof -ti:8080 | xargs kill -9
python3 app.py

# If using Docker
docker-compose restart
```

## 📊 Verify It Works

1. Start the application
2. Select "P0_Streaming" checkbox
3. Choose time range (4 hours recommended for testing)
4. Click "Send"
5. Should see data within 30-60 seconds

## 🆘 Still Not Working?

If you've tried everything above:

1. **Check Splunk Status:**
   - https://status.splunk.com
   - Verify Splunk Cloud is operational

2. **Review Logs:**
   ```bash
   tail -f agent_tool_logs.log
   ```
   Look for specific error messages

3. **Test with curl:**
   ```bash
   curl -k -H "Authorization: Bearer YOUR_TOKEN" \
     "https://arlo.splunkcloud.com:8089/services/search/jobs/export" \
     -d "search=search index=* | head 1" \
     -d "output_mode=json" \
     --connect-timeout 30
   ```
   This will show the exact API response.

4. **Alternative: Use Splunk Web UI**
   - Until API access is resolved
   - Access dashboards directly at:
     - https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard

## 💡 Pro Tips

1. **For Development:**
   - Get your home IP and office IP whitelisted
   - Use VPN to route all traffic through whitelisted IP

2. **For Production:**
   - Whitelist server/container IP ranges
   - Document IPs in runbook
   - Set up monitoring for Splunk API availability

3. **Backup Plan:**
   - Comment out Splunk tools if not immediately needed
   - Use other monitoring tools (Datadog, PagerDuty)
   - Re-enable once whitelisting is complete

## 📞 Contact Information

**Splunk Support:**
- Internal: splunk-admin@verisure.com
- Splunk Cloud: https://www.splunk.com/en_us/support.html

**GOC_AgenticAI Issues:**
- Create issue in repository
- Contact: Jorge Gil (application author)

---

## 🎯 Summary

**Most likely issue:** IP not whitelisted in Splunk Cloud  
**Fastest fix:** Get your IP added to Splunk allowlist  
**Time to resolve:** Usually 1-24 hours after admin adds IP  
**Alternative:** Use VPN that routes through whitelisted IP range

**After whitelisting, Splunk tools will work immediately - no code changes needed!**
