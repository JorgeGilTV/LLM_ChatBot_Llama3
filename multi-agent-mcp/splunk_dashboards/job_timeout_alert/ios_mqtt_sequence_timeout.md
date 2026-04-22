# Splunk — iOS `DevicesSubscriptionsManagerMQTT` / `Sequence timeout`

Client logs (`kube:container:backend-log-server`) when MQTT topic subscription hits **Sequence timeout** in `subscribeOnTopics`. Use this to **count**, **group by device**, and **spot repeats** — not every raw row is a separate “hang”; many lines can share one client burst.

Replace `YOUR_CID` or drop the `CID=` clause for all accounts (careful in prod).

---

## 1. Tighter base search (recommended)

Uses extracted fields when Splunk parses them; narrows noise vs a bare `error` token.

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
SV="6.19*"
```

If `SV` is not a field, keep version on `_raw`:

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
"6.19.0"
```

---

## 2. Extract `deviceId` from `MSG`

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
| rex field=MSG "deviceId:\s*(?<device_id>\S+)"
| stats count AS events by device_id
| sort - events
```

---

## 3. How many distinct devices vs raw event count

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
| rex field=MSG "deviceId:\s*(?<device_id>\S+)"
| stats count AS raw_events dc(device_id) AS distinct_devices
```

---

## 4. Incidents over time (not per-device spam)

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
| rex field=MSG "deviceId:\s*(?<device_id>\S+)"
| timechart span=1h count AS timeouts dc(device_id) AS devices_affected
```

---

## 5. One row per (time bucket × device) — reduce duplicate feel

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
CID="YOUR_CID"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
| rex field=MSG "deviceId:\s*(?<device_id>\S+)"
| bin _time span=1m
| stats count BY _time device_id
| sort - count
```

---

## 6. Optional: correlate with app build from `SV`

```splunk
... same base ...
| stats count BY SV device_id
| sort - count
```

---

## Interpretation (for triage)

- **Sequence timeout** here is the **iOS client** failing to complete an MQTT subscribe step for that `deviceId` within the expected window — often network, broker load, client backlog, or too many topics at once.
- **232 lines** can be **one session** timing out across **many devices** (your sample shows the same `_time` and client `TS` with different `deviceId`s).
- Correlation with “feeds stuck / delete UI weird” is **hypothesis**: if subscriptions are broken, real-time updates may lag; prove with timelines (timeouts vs user actions), not from this log alone.

---

## Alert (example)

Trigger if distinct devices affected in 1h exceeds a threshold:

```splunk
index="*prod" sourcetype="kube:container:backend-log-server"
LL=ERROR
"DevicesSubscriptionsManagerMQTT"
"Sequence timeout"
earliest=-1h
| rex field=MSG "deviceId:\s*(?<device_id>\S+)"
| stats dc(device_id) AS affected
| where affected > 50
```

Tune threshold and add `CID` if the alert should be per user.
