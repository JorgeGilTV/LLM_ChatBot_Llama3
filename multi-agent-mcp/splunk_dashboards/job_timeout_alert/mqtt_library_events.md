# Splunk searches — `MqttEventSender` / library MQTT (`mediaUploadNotification`)

Sample log line pattern:

- Class: `MqttEventSender`
- Text: `Event has been successfully sent using client:[MQTT]`
- Topic shape: `u/<ownerId>/in/library/add`
- Payload mentions: `resource":"mediaUploadNotification"`, `deviceId`, `ownerId`, `recordingStopped`, presigned URLs (often redacted in exports)

**Note:** This is a **single success** event per publish, not a start/end job pair. The **jobs not finished after 1 hour** dashboard needs logs that express lifecycle (`started` → `completed`/`failed` or similar). Use this doc to **hunt, correlate, and volume-check** library/MQTT notifications.

Replace `YOUR_INDEX` and `YOUR_SOURCETYPE` (or use `index=*` only in dev).

---

## 1. All successful library MQTT sends (broad)

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
"MqttEventSender" "Event has been successfully sent" "in/library/add"
| head 500
```

---

## 2. Extract `requestId`, `eventId`, `userId`, `deviceId`, topic (rex on `_raw`)

Adjust the regex if your timestamp/format differs at the start of the line.

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
"MqttEventSender" "in/library/add"
| rex field=_raw "\[requestId:(?<request_id>[^\]]+)\]"
| rex field=_raw "\"eventId\":\"(?<event_id>[^\"]+)\""
| rex field=_raw max_match=0 "\"userId\":\"(?<user_id>[^\"]+)\""
| rex field=_raw "\"deviceId\":\"(?<device_id>[^\"]+)\""
| rex field=_raw "topic:\[(?<mqtt_topic>[^\]]+)\]"
| table _time request_id event_id user_id device_id mqtt_topic
| sort - _time
```

If `userId` appears multiple times in `_raw`, the first match may be enough for triage; refine with `max_match` or a stricter rex if needed.

---

## 3. Filter by account / device

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
"MqttEventSender" "in/library/add" "S92F6-300-13158043" "AKJ158EM00E81"
| sort - _time
```

Use the real `ownerId` / `userId` and `deviceId` from your case.

---

## 4. `mediaUploadNotification` only

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
"MqttEventSender" "mediaUploadNotification" "recordingStopped"
| stats count by deviceId ownerId
```

If `deviceId` is **not** extracted as a field yet, search on `_raw`:

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
_raw="*mediaUploadNotification*" AND _raw="*MqttEventSender*"
| timechart span=1h count
```

---

## 5. Volume / anomalies (baseline)

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-7d
"MqttEventSender" "in/library/add"
| timechart span=1h count
```

---

## 6. Failures or errors (if you log them with a different message)

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
"MqttEventSender" ("ERROR" OR "error" OR "failed" OR "Failed")
| sort - _time
```

Tune keywords to your actual failure strings.

---

## Optional: `spath` if the full MQTT payload is indexed JSON

If Splunk parses nested JSON into fields, you can try:

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-24h
| spath path=resource output=resource
| search resource="mediaUploadNotification"
```

This only works if the JSON structure matches what `spath` expects on your sourcetype.

---

## Linking to “job not done in 1 hour”

To reuse the **job timeout** dashboard logic you need **two sides** of a workflow, for example:

- Log A: upload / notification **queued** or **processing started** (with a shared id).
- Log B: same id **completed** or **failed**.

If you only have this **success MQTT** line, you can still alert on **gaps** only if another log marks “processing started” and you join on `requestId`, `eventId`, or a recording id parsed from the URL/path.

---

## Privacy

Presigned URLs in logs are sensitive. Prefer searching on **ids** (`deviceId`, `ownerId`, `eventId`, `requestId`, filename stem like `1774033666849.mp4`) rather than sharing full `_raw` in tickets.
