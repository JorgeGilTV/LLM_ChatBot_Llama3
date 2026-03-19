# Status Monitor Configuration Guide

## ⚙️ Monitoring Modes

The Status Monitor supports two modes for health detection:

### 🟢 Fast Mode (Default - Recommended)

**Enabled by:** `enable_extended_metrics=False` in `status_monitor.py`

**What it checks:**
- ✅ Request count (trace.servlet.request.hits)
- ✅ Error count (trace.servlet.request.errors)
- ✅ Error rate calculation

**Status thresholds:**
- `critical`: Error rate > 5% OR active PagerDuty incident (last 24h, triggered/acknowledged only)
- `warning`: Error rate > 1%
- `healthy`: Error rate ≤ 1%
- `inactive`: No traffic (filtered out, not shown)

**Advantages:**
- ⚡ Faster (~2-3 API calls per service)
- 🎯 More accurate (fewer false positives)
- 💰 Lower API costs
- 🔥 Better performance for large service counts

**Recommended for:** Production monitoring, real-time dashboards

---

### 🔴 Extended Mode (Advanced)

**Enabled by:** `enable_extended_metrics=True` in `status_monitor.py`

**Additional checks:**
- 📊 P95 latency monitoring
- 📊 P99 latency monitoring  
- 📉 Traffic drop detection (vs **7-day average** baseline)
- ⚠️ High latency alerts
- 📈 Traffic variance comparison

**Status thresholds (additional to Fast Mode):**
- `critical`: Traffic drop > 85% vs 7-day average (minimum 5000 req/week)
- `warning`: High latency (P95 > 2000ms or P99 > 5000ms)

**Weekly Baseline Benefits:**
- ✅ Accounts for weekly patterns (weekday vs weekend traffic)
- ✅ Reduces false positives from daily variations
- ✅ More stable baseline than 24-hour comparison
- ✅ Shows traffic variance in tooltips

**Advantages:**
- 🔬 More comprehensive health checks
- 📈 Latency trend detection
- 🚨 Early warning for degradation

**Disadvantages:**
- 🐌 Slower (~5-6 API calls per service)
- ⚠️ More false positives (normal traffic variations flagged)
- 💸 Higher API costs

**Recommended for:** Deep investigations, post-mortems, specific service analysis

---

## 🎯 Which Mode to Use?

### Use Fast Mode (default) when:
- Monitoring production in real-time
- You have many services (>30)
- You want quick, reliable status
- Error rate is your primary concern

### Use Extended Mode when:
- Investigating a specific outage
- Analyzing performance degradation
- Doing capacity planning
- Need historical latency trends (with API/persistence enabled)

---

## 🔧 Configuration

### Enable Extended Metrics

Edit `tools/status_monitor.py`:

```python
# Find this line (around line 696):
future = executor.submit(
    get_service_health_status,
    service, env, dd_api_key, dd_app_key, dd_site,
    from_time, current_time,
    False  # Change to True to enable extended metrics
)
```

### Adjust Thresholds

Edit `tools/status_monitor.py`:

```python
# Error rate thresholds (line ~426)
elif error_rate > 5:        # Critical threshold (was 3%)
    status = 'critical'
elif error_rate > 1:        # Warning threshold (was 0.5%)
    status = 'warning'

# Latency thresholds (line ~402)
if p95_latency and p95_latency > 2000:  # 2 seconds
    high_latency = True
if p99_latency and p99_latency > 5000:  # 5 seconds
    high_latency = True

# Traffic drop threshold (line ~404) - uses 7-day baseline
baseline_from = from_time - (7 * 86400)  # 7 days before
baseline_to = from_time
if current_rate < (baseline_avg_rate * 0.15):  # 85% drop
    traffic_drop = True
```

---

## 📊 Latency Metrics Details

### Metric Format

Datadog returns latency in **SECONDS** for `duration.by.service.XXp` metrics:

```
API Response: 0.145 = 145 milliseconds
API Response: 2.5 = 2500 milliseconds (2.5 seconds)
API Response: 15.0 = 15000 milliseconds (15 seconds)
```

### Auto-Detection Logic

The code automatically detects units to avoid double conversion:

```python
if avg_latency > 100:
    # Value > 100 is unrealistic as seconds (100s = 100,000ms)
    # Assume already in milliseconds
    p95_latency = avg_latency
else:
    # Standard case: convert seconds to milliseconds
    p95_latency = avg_latency * 1000
```

### Troubleshooting

If latency values look wrong:

1. **Check debug logs** - look for lines like:
   ```
   ✅ service-name (env): P95 = 145.23ms (converted from 0.145s)
   ```

2. **Compare with Datadog UI:**
   - Open service in APM
   - Check P95 latency value
   - Should match our display

3. **Run diagnostic:**
   ```bash
   # Check what Datadog is returning
   python3 debug_metrics.py <service-name> <environment>
   ```

---

## 🚨 PagerDuty Correlation Logic

The monitor automatically correlates PagerDuty incidents with services:

**Filtering Rules:**
1. ✅ Only **ACTIVE** incidents (status = `triggered` or `acknowledged`)
2. ❌ **Ignores** resolved incidents (even if recent)
3. ✅ Only incidents from **last 24 hours**
4. ❌ **Ignores** old incidents (> 24h ago)

**Matching Logic:**
- Extracts service names from incident title and service summary
- Matches against monitored service names (case-insensitive)
- Marks matched services as `critical` with 🚨 badge
- Logs matched incidents in console for visibility

**Example Log:**
```
🔍 Filtering 8 PagerDuty incidents (triggered + acknowledged only, last 24h)...
  🚨 Incident [TRIGGERED]: 'backend-auth high error rate...' → backend-auth
  🚨 Incident [ACKNOWLEDGED]: 'camsdk timeout issues...' → camsdk-webserver
🔗 PagerDuty correlation: 2 recent active incidents → 2 services affected
```

**Impact:**
- ✅ Services with active PagerDuty alerts are marked critical
- ✅ Services with resolved alerts return to normal status (based on metrics)
- ✅ No false positives from old/resolved incidents

---

## 🚨 Common Issues

### Issue: Many services showing red (critical)

**Cause:** Extended metrics enabled with aggressive traffic drop detection

**Solution:** 
1. Disable extended metrics (set to `False`)
2. Or increase traffic drop threshold
3. Or increase minimum baseline traffic requirement

### Issue: Latency showing 0ms when service has traffic

**Cause:** Metric pattern mismatch or no latency data

**Solutions:**
1. Service may use `trace.http.*` instead of `trace.servlet.*`
2. Check service in Datadog APM UI to see actual metric name
3. Run `python3 inspect_service_metrics.py <service>`

### Issue: Latency showing extremely high (50,000ms+)

**Cause:** Wrong unit detection or metric type

**Solutions:**
1. Check debug logs to see raw values
2. Verify metric returns seconds (should be < 100 typically)
3. Adjust threshold in auto-detection logic

---

## 💡 Recommendations

**For production monitoring:**
```python
enable_extended_metrics = False  # Fast, stable, fewer false positives
```

**For specific investigations:**
```python
enable_extended_metrics = True   # Comprehensive, slower, more details
```

**Cache settings:**
```python
_cache_ttl = 120  # 2 minutes - good balance for production
```

**Parallel workers:**
```python
max_workers = 15  # Good balance for 50+ services
```

---

## 🔍 Debug Commands

```bash
# View logs when loading dashboard
docker logs -f oneview-goc

# Test specific service
python3 verify_camsdk.py

# Inspect available metrics
python3 inspect_service_metrics.py backend-camsdk-webserver goldenqa

# Test different metric patterns
python3 debug_metrics.py backend-camsdk-webserver goldenqa
```
