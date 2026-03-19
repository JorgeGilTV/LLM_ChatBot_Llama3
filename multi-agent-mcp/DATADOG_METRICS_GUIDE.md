# Datadog Metrics - Troubleshooting Guide

## 🔍 Understanding Latency Metrics

### Metric Units in Datadog APM

Datadog APM latency metrics come in **different units** depending on the metric type:

1. **`trace.*.request.duration.by.service.XXp`** → Returns in **SECONDS**
   - Example: `0.145` = 145 milliseconds
   - **Need to multiply by 1000** to get milliseconds

2. **`trace.*.request.duration`** (without percentile) → Returns in **NANOSECONDS**
   - Example: `145000000` = 145 milliseconds  
   - **Need to divide by 1,000,000** to get milliseconds

3. **APM Service Page UI** → Shows in **MILLISECONDS**
   - What you see in the Datadog UI is already converted

### Current Implementation

Our code converts from seconds to milliseconds:

```python
# In status_monitor.py and datadog_dashboards.py
p95_latency = (sum(valid_latencies) / len(valid_latencies)) * 1000
```

## 🐛 Common Issues

### Issue 1: Showing 0ms when service has traffic

**Symptoms:**
- Service shows requests > 0
- Latency shows 0ms
- Datadog UI shows latency data

**Causes:**
1. Metric name doesn't match (service uses different pattern)
2. Missing data in time range
3. Metric is under different namespace (not trace.servlet.*)

**Solutions:**
```bash
# Run diagnostic to find correct metric name
python3 inspect_service_metrics.py <service-name> <environment>

# Test different patterns
python3 debug_metrics.py <service-name> <environment>
```

### Issue 2: Showing extremely high latency (>10s)

**Symptoms:**
- Latency shows 50,000ms or higher
- Datadog UI shows normal latency (~50ms)

**Causes:**
1. Double conversion (already in ms, multiplied by 1000 again)
2. Wrong metric returning nanoseconds instead of seconds
3. Summing instead of averaging

**Check:**
```python
# Look at raw values in debug logs:
# If values are like: [0.050, 0.145, 0.233] → SECONDS (correct)
# If values are like: [50, 145, 233] → MILLISECONDS (don't multiply)
# If values are like: [50000000, 145000000] → NANOSECONDS (divide by 1M)
```

### Issue 3: High error rate showing when Datadog shows 0%

**Symptoms:**
- Error rate shows 5% in app
- Datadog UI shows 0% or very low

**Causes:**
1. Wrong time range
2. Counting different error types
3. Aggregation mismatch

**Check:**
- Compare total requests count (should match Datadog)
- Compare total errors count
- Verify time range matches

## 🔧 Debugging Steps

### Step 1: Check Raw Metric Values

Add debug logging to see actual values returned:

```python
print(f"Raw latency values: {valid_latencies}")
print(f"Average before conversion: {sum(valid_latencies) / len(valid_latencies)}")
print(f"After conversion: {(sum(valid_latencies) / len(valid_latencies)) * 1000}")
```

### Step 2: Verify Metric Name

Check what Datadog is actually using for this service:

1. Open service in Datadog APM UI
2. Click on "Latency" graph
3. Click "Edit" or inspect the query
4. Look at the metric name in the query

Common variations:
- `trace.servlet.request.duration.by.service.95p`
- `trace.http.request.duration.by.service.95p`
- `trace.web.request.duration.by.service.95p`
- `trace.aspnet.request.duration.by.service.95p`
- `trace.rack.request.duration.by.service.95p`

### Step 3: Check Time Range

Ensure the time range matches:
- App uses: `current_time - (timerange_hours * 3600)`
- Datadog UI: Check selected time range in top right

### Step 4: Verify Service Name Exactly

Service names must match exactly (case-sensitive):
- ✅ `backend-camsdk-webserver` 
- ❌ `camsdk-webserver` (if Datadog has the prefix)
- ❌ `backend_camsdk_webserver` (underscore vs hyphen)

## 🎯 Specific Fix for camsdk-webserver

Based on the reported issue, possible causes:

1. **If showing very high latency (50,000ms+):**
   - Metric might already be in milliseconds
   - Need to remove the `* 1000` conversion

2. **If showing correct latency but high error rate:**
   - Service might actually have errors in goldenqa
   - Verify in Datadog UI if errors match
   - Check if it's a test environment with synthetic errors

3. **If metric name is wrong:**
   - Service might use `trace.http.*` instead of `trace.servlet.*`
   - Try adding support for multiple trace types

## 📝 Quick Test Commands

```bash
# Test a specific service
python3 verify_camsdk.py

# Or manually check in Datadog:
# 1. Go to: https://arlo.datadoghq.com/apm/services
# 2. Find: backend-camsdk-webserver
# 3. Select environment: goldenqa
# 4. Time range: Last 4 hours
# 5. Compare values with what the app shows
```

## 🔄 What to Do Next

1. Run the app and look at the debug logs (they now show detailed metric info)
2. Compare with Datadog UI values
3. If values don't match, check:
   - Are raw values in seconds or milliseconds?
   - Is the metric name correct?
   - Is the time range correct?
4. Adjust conversion factor if needed
