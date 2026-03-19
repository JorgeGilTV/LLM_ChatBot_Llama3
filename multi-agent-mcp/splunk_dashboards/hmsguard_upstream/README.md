# Splunk Dashboard: PartnerAPI nginx → hmsguard-ss-qa Upstream Health

Dashboard to troubleshoot **502 (no live upstreams)** and **upstream timed out (110)** when PartnerAPI nginx proxies to the `hmsguard-ss-qa` upstream (RapidSOS callbacks).

---

## Brief description (for creating the dashboard)

Use this when creating or documenting the dashboard in Splunk:

> **PartnerAPI nginx → hmsguard-ss-qa upstream health**  
> Monitors nginx reverse-proxy errors when forwarding RapidSOS callbacks to the hmsguard-ss-qa service: "no live upstreams" (502) and "upstream timed out" (110). Use to identify when the QA upstream has no healthy backends or when backends time out, and to cross-reference with K8s pod IPs, HPA scaling, and DNS. Adjust index, host, and sourcetype to match your nginx error log source.

---

## Root cause (summary)

- **no live upstreams:** nginx resolved `hmsguard-ss-qa` but all backends failed health checks → no healthy pods or wrong DNS.
- **upstream timed out (110):** nginx reached the backend IP:port but got no response → pods down or overloaded.

The issue is in the **hmsguard-ss-qa** (QA) service, not in nginx or Cloudflare.

---

## How to import the dashboard in Splunk

1. In Splunk: **Apps → Manage Apps → Create app** (or use an existing app, e.g. Search).
2. Go to **Dashboards** (or **Create → Dashboard**).
3. **Create New Dashboard** → **Import from file** (or equivalent in your version).
4. Upload or paste the contents of `hmsguard_upstream_dashboard.xml`.
5. Save the dashboard (e.g. name: **PartnerAPI hmsguard upstream health**).

If your Splunk has no "Import from file":

- Create a new dashboard, then **Edit → Edit Source** (or "Edit in XML") and paste the full XML from `hmsguard_upstream_dashboard.xml`.

---

## Customize index / host / sourcetype

In the XML, all searches use `index=*`. Adjust for your environment:

- **Only PartnerAPI nginx logs:**  
  `index=YOUR_NGINX_INDEX host=*partnerapi*`  
  (or the index/sourcetype where nginx error logs are stored).
- **Specific host:**  
  `index=* host=nginx-partnerapi-prod-us-z2-10.18.133.157`.

Use **Find and Replace** in the XML:

- Find: `index=*`  
- Replace with: `index=your_index host=*partnerapi*`  
  (or your preferred base).

Save and reload the dashboard.

---

## Files in this folder

| File | Purpose |
|------|---------|
| `hmsguard_upstream_dashboard.xml` | Simple XML dashboard to import in Splunk. |
| `SPL_QUERIES.md` | SPL searches to copy/paste in Search or other dashboards. |
| `README.md` | This guide. |

---

## Next steps (investigation)

1. **HPA:** Does `hmsguard-ss-qa` scale to 0 replicas in QA? → Check `minReplicas` (e.g. ≥ 1 if it must always be available).
2. **DNS:** Verify that `hmsguard-ss-qa` resolves to the correct K8s Service in the QA namespace.
3. **Probes:** Review readiness/liveness probes for hmsguard pods in QA; if they fail, nginx marks backends down and "no live upstreams" appears.
