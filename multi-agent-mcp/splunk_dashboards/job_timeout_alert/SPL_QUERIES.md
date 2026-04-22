# SPL — Jobs not finishing within 1 hour

Copy these searches into **Search** or use them as the basis for an **alert** (same logic as the dashboard).

## Prerequisites

Your logs must include:

- A per-job identifier: here **`job_id`** is used.
- A **`status`** field with at least:
  - start: `started` (change the value if you use `RUNNING`, `queued`, etc.)
  - end: `completed` or `failed` (change if you use `SUCCESS`, `ERROR`, …).

If names differ, update the literals and the `match()` in the SPL.

## Main search (table + alert)

Replace `YOUR_INDEX` and `YOUR_SOURCETYPE`:

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-48h
( status="started" OR status="completed" OR status="failed" )
| stats earliest(eval(if(status="started",_time,null()))) AS start_t latest(eval(if(match(status,"completed|failed"),_time,null()))) AS end_t BY job_id
| where isnotnull(start_t) AND isnull(end_t)
| eval runtime_sec = now() - start_t
| where runtime_sec > 3600
| eval runtime_min = round(runtime_sec/60,1)
| eval start_human=strftime(start_t,"%Y-%m-%d %H:%M:%S %Z")
| table job_id start_human runtime_min runtime_sec
| sort - runtime_sec
```

## Count only (Single Value panel)

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-48h
( status="started" OR status="completed" OR status="failed" )
| stats earliest(eval(if(status="started",_time,null()))) AS start_t latest(eval(if(match(status,"completed|failed"),_time,null()))) AS end_t BY job_id
| where isnotnull(start_t) AND isnull(end_t)
| eval runtime_sec = now() - start_t
| where runtime_sec > 3600
| stats count AS stale_jobs
```

## Create the alert in Splunk

1. Paste the **main search** into Search and verify results.
2. **Save As → Alert**.
3. **Trigger**: number of results **greater than 0**.
4. **Schedule**: every 5 or 15 minutes (as needed).
5. **Report time range**: consistent with `earliest=-48h` (or whatever you use).
6. **Throttle** (optional): avoid repeating the same alert for the same `job_id` if your Splunk supports it.
7. Action: email, Slack, webhook, etc.

The **dashboard** is for visibility only; the **alert** sends notifications.

## Variant: single event per job with `state`

If you do not have separate events but an updated `state` field:

```splunk
index=YOUR_INDEX sourcetype=YOUR_SOURCETYPE earliest=-48h
| stats latest(state) AS state latest(_time) AS last_event earliest(_time) AS created BY job_id
| where state!="completed" AND state!="failed"
| eval runtime_sec = now() - created
| where runtime_sec > 3600
| table job_id created state runtime_sec
```

Adjust `state` values to match your model.
