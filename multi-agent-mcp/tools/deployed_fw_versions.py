"""
Deployed firmware matrix for Arlo — **data source: updates.arlo.com JSON** (not the SPA HTML).

The internal dashboard (deployed-fw-versions.arlocloud.com) renders tables in the browser; the server
HTML has no <table>. We fetch:

  https://updates.arlo.com/arlo/fw/fw_deployed/{env}/updaterules/{MODEL}_UpdateRules.json

Model families are discovered from embedded URLs in the dashboard HTML when credentials allow,
or from DEPLOYED_FW_MODEL_FAMILIES and/or a built-in fallback list.

Optional: ATLASSIAN_EMAIL/CONFLUENCE_TOKEN or ARLO_USER/ARLO_PASSWORD to load the dashboard HTML
for URL discovery (VPN may be required). JSON from updates.arlo.com is fetched without auth.
"""

from __future__ import annotations

import html as html_mod
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_URL = "https://deployed-fw-versions.arlocloud.com"
_UPDATES_BASE = "https://updates.arlo.com/arlo/fw/fw_deployed"
_MAX_HTML_SCAN = 2_000_000
_MAX_FAMILIES = 90
_MAX_WORKERS = 14

# When HTML discovery finds nothing, try these common family keys (same names as in UpdateRules URLs).
_DEFAULT_MODEL_FAMILIES: tuple[str, ...] = (
    "VMB3010",
    "VMB3500",
    "VMB4000",
    "VMB4500",
    "VMB4540",
    "VMB4600",
    "VMB5000",
    "VMC2020",
    "VMC2030",
    "VMC2040",
    "VMC2050",
    "VMC3030",
    "VMC3040",
    "VMC4030",
    "VMC4040",
    "VMC4040P",
    "VMC4041",
    "VMC4050",
    "VMC4060",
    "VMC5040",
    "VMC6050",
    "VMC6060",
    "AVD1001",
    "AC2001",
)

_DASH_WRAP = """
<div class="arlo-fw-versions-dash" style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#ffffff;color:#0f172a;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
  <div style="padding:20px 22px 16px;border-bottom:1px solid #e5e7eb;background:linear-gradient(180deg,#f8fafc 0%,#fff 100%);">
    <h1 style="margin:0 0 6px;font-size:1.35rem;font-weight:800;letter-spacing:-0.03em;color:#0f172a;">Arlo FW Versions</h1>
    <p style="margin:0;font-size:0.85rem;color:#64748b;line-height:1.45;">
      Firmware from <strong>updates.arlo.com</strong> (UpdateRules JSON). The SPA at
      <a href="{url}/" target="_blank" rel="noopener" style="color:#0284c7;font-weight:600;">deployed-fw-versions</a>
      only loads tables in the browser — this tool calls the public JSON API directly.
    </p>
  </div>
  <div style="padding:16px 18px 20px;">
{body}
  </div>
</div>
"""


def _requests_auth():
    arlo_user = os.getenv("ARLO_USER")
    arlo_password = os.getenv("ARLO_PASSWORD")
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if arlo_user and arlo_password:
        return (arlo_user, arlo_password)
    if email and token:
        return (email, token)
    return None


def _normalize_cell_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# Words that only describe the question / env — never used to narrow table rows.
_ROW_FILTER_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "what",
        "which",
        "show",
        "tell",
        "give",
        "list",
        "please",
        "need",
        "want",
        "check",
        "query",
        "search",
        "about",
        "current",
        "deployed",
        "deployment",
        "firmware",
        "fw",
        "version",
        "versions",
        "matrix",
        "table",
        "arlo",
        "me",
        "my",
        "get",
        "display",
        "see",
        "find",
        "look",
        "up",
        "at",
        "from",
        "with",
        "using",
        "tool",
        "dashboard",
        # env is chosen via _parse_env_from_query, not row text
        "production",
        "prod",
        "dev",
        "development",
        "qa",
        "staging",
        "field",
        "trial",
        "fieldtrial",
    }
)


def _row_filter_tokens(query: str) -> list[str]:
    """Tokens that must appear in the row (any cell); empty list = show all rows."""
    q = (query or "").lower()
    for phrase in ("field trial", "fieldtrial"):
        q = q.replace(phrase, " ")
    words = re.findall(r"[a-z0-9]+", q)
    out: list[str] = []
    for w in words:
        if len(w) < 2 or w in _ROW_FILTER_STOPWORDS:
            continue
        out.append(w)
    return out


def _row_matches_filter(cells: list[str], tokens: list[str]) -> bool:
    if not tokens:
        return True
    blob = " ".join(_normalize_cell_text(c).lower() for c in cells)
    return all(tok in blob for tok in tokens)


def _cell_style(header: str, value: str) -> str:
    h = (header or "").lower().strip()
    v = _normalize_cell_text(value)
    base = "border:1px solid #e2e8f0;padding:8px 10px;font-size:12px;vertical-align:middle;"
    if v in ("", "-", "—", "N/A", "n/a"):
        return base + "color:#94a3b8;font-weight:600;"
    if "model" in h and "rollout" not in h:
        return base + "font-weight:700;color:#0f172a;"
    if "codename" in h or "code name" in h:
        return base + "color:#334155;font-weight:500;"
    if "production" in h and "version" in h:
        return base + "color:#15803d;font-weight:600;"
    if "rollout" in h and "%" in h:
        return base + "color:#2563eb;font-weight:700;"
    if "rollout" in h and ("fw" in h or "firmware" in h):
        return base + "color:#c2410c;font-weight:600;"
    if "version" in h and "rollout" not in h:
        return base + "color:#15803d;font-weight:600;"
    return base + "color:#1e293b;"


def _thead_html(headers: list[str]) -> str:
    cells = []
    for h in headers:
        cells.append(
            "<th style=\"border:1px solid #1e3a5f;padding:10px 10px;background:#1e3a8a;color:#f8fafc;"
            "text-align:left;font-size:11px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;\">"
            + html_mod.escape(h)
            + "</th>"
        )
    return "<thead><tr>" + "".join(cells) + "</tr></thead>"


def _parse_env_from_query(query: str) -> str:
    """Map user text to updates.arlo.com path segment."""
    q = (query or "").lower()
    if "staging" in q:
        return "staging"
    if "field trial" in q or "fieldtrial" in q.replace(" ", ""):
        return "fieldtrial"
    if re.search(r"\bqa\b", q):
        return "qa"
    if re.search(r"\bdev\b", q):
        return "dev"
    return "production"


def _discover_families_from_html(html: str) -> list[str]:
    if not html:
        return []
    found = set(re.findall(r"updaterules/([A-Za-z0-9]+)_UpdateRules\.json", html, flags=re.I))
    return sorted(found)


def _env_families_override() -> list[str]:
    raw = (os.getenv("DEPLOYED_FW_MODEL_FAMILIES") or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _merge_families(html: str) -> list[str]:
    discovered = _discover_families_from_html(html[:_MAX_HTML_SCAN])
    override = _env_families_override()
    merged = list(dict.fromkeys(discovered + override + list(_DEFAULT_MODEL_FAMILIES)))
    merged.sort(key=lambda x: (len(x), x))
    return merged[:_MAX_FAMILIES]


def _codename_from_description(description: str, family: str) -> str:
    if not description:
        return "—"
    d = description
    d = re.sub(re.escape(family), "", d, flags=re.I).strip()
    d = re.sub(r"(?i)\b(camera|basestation)\b", "", d).strip()
    d = re.sub(r"\d+(?:\.\d+)+[^\s,]*", "", d)
    d = re.sub(r"\s+", " ", d).strip(" ,-|")
    return d[:160] if d else "—"


def _first_model_row(family: str, data: dict[str, Any]) -> dict[str, str] | None:
    models = data.get("models")
    if not isinstance(models, list) or not models:
        return None
    m0 = models[0]
    if not isinstance(m0, dict):
        return None
    ver = _normalize_cell_text(str(m0.get("version") or ""))
    desc = ""
    dp = m0.get("defaultPath")
    if isinstance(dp, list) and dp and isinstance(dp[0], dict):
        desc = str(dp[0].get("description") or "")
    codename = _codename_from_description(desc, family)
    # Rollout buckets often require rv cookie — not in static JSON
    return {
        "MODEL": family,
        "CODENAME": codename if codename != "—" else _normalize_cell_text(str(m0.get("modelId") or "—")),
        "PRODUCTION VERSION": ver or "—",
        "ROLLOUT FW": "—",
        "ROLLOUT %": "—",
    }


def _fetch_update_rules(env: str, family: str) -> tuple[str, dict[str, Any] | None, str | None]:
    url = f"{_UPDATES_BASE}/{env}/updaterules/{family}_UpdateRules.json"
    try:
        r = requests.get(url, timeout=(4, 18), headers={"Accept": "application/json"})
        if r.status_code != 200:
            return family, None, f"HTTP {r.status_code}"
        return family, r.json(), None
    except Exception as e:
        return family, None, str(e)[:120]


def _render_table(rows: list[dict[str, str]], labels: list[str]) -> str:
    parts = [
        '<table style="border-collapse:collapse;width:100%;margin:0 0 18px 0;min-width:520px;">',
        _thead_html(labels),
        "<tbody>",
    ]
    for row in rows:
        parts.append("<tr>")
        for lab in labels:
            cell = row.get(lab, "—")
            st = _cell_style(lab, cell)
            parts.append(f"<td style=\"{st}\">{html_mod.escape(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def read_deployed_fw_versions(query: str) -> str:
    q = (query or "").strip()
    env = _parse_env_from_query(q)

    raw_html = ""
    auth = _requests_auth()
    page_note = ""
    if auth:
        try:
            resp = requests.get(
                _BASE_URL.rstrip("/") + "/",
                auth=auth,
                timeout=(8, 22),
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            )
            if resp.status_code == 200 and resp.text:
                raw_html = resp.text
                page_note = "Loaded dashboard HTML for model discovery (VPN may be required)."
            else:
                page_note = f"Dashboard returned HTTP {resp.status_code}; using defaults + env model list."
        except requests.exceptions.RequestException as e:
            page_note = f"Dashboard unreachable ({html_mod.escape(str(e)[:200])}); using defaults + env."
    else:
        page_note = (
            "No ARLO/Atlassian credentials — skipped internal dashboard. "
            "Set credentials to discover all model URLs from the SPA bundle, or set "
            "<code>DEPLOYED_FW_MODEL_FAMILIES</code> in <code>.env</code>."
        )

    families = _merge_families(raw_html)
    if not families:
        return (
            "<p><strong>Deployed FW versions</strong>: no model families to query. "
            "Set <code>DEPLOYED_FW_MODEL_FAMILIES=VMB5000,VMC4030,...</code> in <code>.env</code>.</p>"
        )

    rows_out: list[dict[str, str]] = []
    errors_sample: list[str] = []
    labels = ["MODEL", "CODENAME", "PRODUCTION VERSION", "ROLLOUT FW", "ROLLOUT %"]
    filter_tokens = _row_filter_tokens(q)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futs = {pool.submit(_fetch_update_rules, env, fam): fam for fam in families}
        for fut in as_completed(futs):
            fam, data, err = fut.result()
            if data is None:
                if len(errors_sample) < 8 and err:
                    errors_sample.append(f"{fam}: {err}")
                continue
            row = _first_model_row(fam, data)
            if row:
                cells = [row[k] for k in labels]
                if _row_matches_filter(cells, filter_tokens):
                    rows_out.append(row)

    rows_out.sort(key=lambda r: r.get("MODEL", ""))

    inner: list[str] = [
        f"<p style='margin:0 0 8px;font-size:12px;color:#475569;'>"
        f"<strong>Environment:</strong> <code>{html_mod.escape(env)}</code> &nbsp;·&nbsp; "
        f"<strong>Families queried:</strong> {len(families)} (max {_MAX_FAMILIES}) &nbsp;·&nbsp; "
        f"<strong>Rows:</strong> {len(rows_out)}</p>",
        f"<p style='margin:0 0 14px;font-size:11px;color:#64748b;'>{html_mod.escape(page_note)}</p>",
    ]
    if filter_tokens:
        inner.append(
            f"<p style='margin:0 0 12px;font-size:12px;color:#475569;'>"
            f"Row filter (tokens): <code>{html_mod.escape(', '.join(filter_tokens))}</code></p>"
        )

    if rows_out:
        inner.append(_render_table(rows_out, labels))
        inner.append(
            "<p style='margin:12px 0 0;font-size:11px;color:#64748b;line-height:1.45;'>"
            "ROLLOUT columns are not available from static JSON (the live UI uses rv cookie / bucket rules). "
            "Source: <code>updates.arlo.com/.../updaterules/&lt;MODEL&gt;_UpdateRules.json</code>."
            "</p>"
        )
    else:
        inner.append(
            "<p style='color:#b91c1c;font-size:13px;'><strong>No rows after JSON fetch.</strong> "
            "Check env name, model list, or VPN. Sample errors: "
            + html_mod.escape("; ".join(errors_sample) or "none")
            + "</p>"
        )

    body = "\n".join(inner)
    return _DASH_WRAP.format(url=_BASE_URL, body=body)
