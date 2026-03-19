# SPL Queries – PartnerAPI nginx → hmsguard-ss-qa Upstream Errors

Use these searches in Splunk to troubleshoot **no live upstreams** and **upstream timed out** when the PartnerAPI nginx reverse proxy forwards requests to `hmsguard-ss-qa` (RapidSOS callbacks).

---

## Adjust index and source

Before using, replace according to your environment:

- **Index:** `index=main` or `index=nginx*` or the index where PartnerAPI nginx logs are stored.
- **Host:** optional `host=*partnerapi*` or `host=*10.18.133.157*` to scope to the affected instance.
- **Sourcetype:** if known, e.g. `sourcetype=nginx:error` or `sourcetype=kube:container:*nginx*`.

Example base you can use in all searches:

```splunk
index=main host=*partnerapi*
```

---

## 1. "no live upstreams" errors (502 – no healthy backends)

All events in the chosen time range:

```splunk
index=* "no live upstreams" "hmsguard-ss-qa"
| eval upstream= coalesce(upstream, "hmsguard-ss-qa")
| table _time, host, upstream, message, _raw
| sort -_time
```

Count over time (timechart, last 24h):

```splunk
index=* earliest=-24h "no live upstreams" "hmsguard-ss-qa"
| timechart span=15m count as "no_live_upstreams"
```

Count by host (which nginx is generating them):

```splunk
index=* earliest=-24h "no live upstreams" "hmsguard-ss-qa"
| stats count by host
| sort -count
```

---

## 2. "upstream timed out" / Connection timed out (110)

Events where the upstream timed out:

```splunk
index=* ( "upstream timed out" OR "Connection timed out" ) ( "110" OR "hmsguard-ss-qa" OR "hmsguard" )
| rex "upstream: (?<upstream>[^\s]+)"
| rex "(\d+\.\d+\.\d+\.\d+:\d+)"
| table _time, host, upstream, _raw
| sort -_time
```

Timechart last 24h:

```splunk
index=* earliest=-24h ( "upstream timed out" OR "Connection timed out" ) ( "110" OR "hmsguard" )
| timechart span=15m count as "upstream_timeouts"
```

Backend IP:port that did not respond (to cross-reference with pods):

```splunk
index=* earliest=-24h ( "upstream timed out" OR "Connection timed out" )
| rex "upstream: (?<upstream_name>[^\s]+)"
| rex "(?<backend_ip>\d+\.\d+\.\d+\.\d+):(?<backend_port>\d+)"
| where isnotnull(backend_ip) AND (upstream_name="hmsguard-ss-qa" OR match(_raw, "hmsguard"))
| stats count by backend_ip, backend_port, upstream_name, host
| sort -count
```

---

## 3. Combined view: both error types

Single search for "no live upstreams" and "upstream timed out" related to hmsguard/RapidSOS:

```splunk
index=* earliest=-24h (
  ("no live upstreams" AND "hmsguard-ss-qa") OR
  (("upstream timed out" OR "Connection timed out") AND ("hmsguard" OR "110"))
)
| rex "upstream: (?<upstream>[^\s]+)"
| eval error_type = case(
  match(_raw, "no live upstreams"), "no_live_upstreams",
  match(_raw, "upstream timed out") OR match(_raw, "Connection timed out"), "upstream_timeout",
  true(), "other"
)
| table _time, host, upstream, error_type, _raw
| sort -_time
```

Timechart by error type:

```splunk
index=* earliest=-24h (
  ("no live upstreams" AND "hmsguard-ss-qa") OR
  (("upstream timed out" OR "Connection timed out") AND ("hmsguard" OR "110"))
)
| eval error_type = case(
  match(_raw, "no live upstreams"), "no_live_upstreams",
  match(_raw, "upstream timed out") OR match(_raw, "Connection timed out"), "upstream_timeout",
  true(), "other"
)
| timechart span=15m count by error_type
```

---

## 4. RapidSOS callback path only

To filter to the RapidSOS callback path:

```splunk
index=* ( "no live upstreams" OR "upstream timed out" OR "Connection timed out" )
  "rapidsos" OR "hmsguard/emergency/provider/rapidsos"
| rex "upstream: (?<upstream>[^\s]+)"
| table _time, host, upstream, _raw
| sort -_time
```

---

## 5. Summary for HPA / pods / DNS investigation

- **"no live upstreams"** → nginx resolved `hmsguard-ss-qa` but no backends passed health checks (zero or all failing pods).
- **"upstream timed out" (110)** → nginx reached the backend IP:port but got no response (pods down or overloaded).

Next steps:

1. **HPA:** Check if `hmsguard-ss-qa` scales to 0 replicas when idle (set min replicas ≥ 1 in QA if it must always be available).
2. **DNS:** Verify that `hmsguard-ss-qa` resolves to the correct K8s Service in the correct namespace.
3. **Probes:** Review readiness/liveness probes for hmsguard pods in QA; if they fail, nginx marks backends down and "no live upstreams" appears.

---

## Using in the dashboard

The dashboard in `hmsguard_upstream_dashboard.xml` uses variants of these searches. After importing, edit the base search or each panel and set `index`, `host`, and `sourcetype` to match your environment.
