# Splunk dashboard: jobs not completed after 1 hour

Dashboard to show **how many jobs** have been running **more than one hour** without an end event (`completed` / `failed`) and to **list them** with runtime. Configure **email/Slack alerts** separately using the same search (see `SPL_QUERIES.md`).

---

## What the SPL does

1. Over a time window (e.g. last 48 hours), groups by `job_id`.
2. Takes the time of the first `started` and the latest `completed` or `failed`.
3. If there is a start but **no** end: computes `now() - start_t`.
4. Keeps rows **only** when that duration is **greater than 3600 seconds** (1 hour).

---

## Import the dashboard into Splunk

1. Log in to Splunk (e.g. `https://arlo.splunkcloud.com`).
2. Open the **app** where you want the dashboard (e.g. **Search** or your SRE app).
3. **Dashboards** → **Create New Dashboard** → if **Import from file** exists, upload `job_timeout_dashboard.xml`.
4. If import is unavailable: **Create** → **Edit** → **Edit Source** / **View XML** and paste the full XML.
5. Save with a clear name, e.g. **Jobs — timeout &gt; 1h**.

---

## Customize before production

| Item | Action |
|------|--------|
| **Index / sourcetype** | Use the dashboard form fields, or edit the XML and replace `$idx$` and `$st$` with fixed values. |
| **`status` values** | If your logs use `RUNNING` instead of `started`, update the SPL in the XML and in `SPL_QUERIES.md`. |
| **Job identifier** | If it is not `job_id`, replace `BY job_id` with your field (`run_id`, `execution_id`, etc.). |
| **`earliest` window** | The XML uses `-48h` so jobs started yesterday are not dropped; extend if jobs can live longer. |

Run a test search in **Search** with your real `index` before relying on the dashboard.

---

## Files

| File | Purpose |
|------|---------|
| `job_timeout_dashboard.xml` | Import or paste into the dashboard XML editor. |
| `SPL_QUERIES.md` | Copy-paste searches and steps to create the **alert**. |
| `README.md` | This guide. |

---

## Summary

- **Dashboard** = visibility (count + table).
- **Alert** = same search saved as a *scheduled alert* when `count > 0`.

If you share `index`, `sourcetype`, and a sample log line (real fields), the SPL in these files can be tightened to your environment.
