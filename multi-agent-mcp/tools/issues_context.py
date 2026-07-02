"""Build compact issues-only context for Bedrock_Report synthesis."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from html import unescape
from typing import Any

_MAX_ISSUES_DEFAULT = 25
_MAX_MCP_JSON_CHARS = 4000

_ISSUE_MARKERS = re.compile(
    r"CRITICAL|HIGH\s*LAT|🔴|🟡|⚠️|triggered|acknowledged|"
    r"ImagePullBackOff|CrashLoop|ErrImagePull|failed\s+pod|"
    r"error\s*rate|403\s+forbidden|elevated|degraded|"
    r"recently\s+resolved|open|in\s+progress|blocked|recurring",
    re.I,
)

_SERVICE_RE = re.compile(r"\b(backend-[a-z0-9-]+|[a-z][a-z0-9]*-[a-z0-9-]+)\b", re.I)
_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_PD_INCIDENT_NUM_RE = re.compile(r"#\s*(\d+)")

_JIRA_DONE = frozenset({"done", "closed", "resolved", "complete", "completed", "cancelled", "canceled"})

_REMEDIATION_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"imagepull|errimagepull", re.I), "Verify image tag exists, ECR/registry auth, and node pull secrets; consider rollback to last known-good image."),
    (re.compile(r"crashloop|oomkilled|exit\s*137", re.I), "Inspect pod logs/events, recent deploy, memory limits, and dependency health; rollback if correlated with release."),
    (re.compile(r"error\s*rate|5\d\d|403|forbidden", re.I), "Correlate with deploy time, upstream dependency errors, auth/token expiry, and traffic shift; check error budget burn."),
    (re.compile(r"high\s*lat|latency|timeout|p99", re.I), "Check DB/query latency, downstream saturation, cache hit rate, and autoscaling headroom."),
    (re.compile(r"disk|volume|pvc|storage", re.I), "Review disk usage trends, PVC binding, retention policies, and expand or prune before full."),
    (re.compile(r"cpu|throttl|memory", re.I), "Validate HPA limits, resource requests/limits, noisy neighbor, and scale-out vs vertical bump."),
    (re.compile(r"mqtt|broker|queue|backlog", re.I), "Inspect broker lag, consumer group health, poison messages, and broker cluster quorum."),
    (re.compile(r"scheduler|cron|job", re.I), "Check last successful run, job concurrency locks, missed schedules, and downstream API failures."),
)


def _is_tool_error_payload(content: str) -> bool:
    head = (content or "").strip().lower()[:200]
    if not head:
        return True
    return bool(
        re.match(
            r"^(error|failed|unable to|exception|traceback|status:\s*(4\d\d|5\d\d))",
            head,
        )
    )


def _strip_html(text: str) -> str:
    t = re.sub(r"<script[^>]*>.*?</script>", " ", text or "", flags=re.I | re.S)
    t = re.sub(r"<style[^>]*>.*?</style>", " ", t, flags=re.I | re.S)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</tr>", "\n", t, flags=re.I)
    t = re.sub(r"</div>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _severity(text: str) -> str:
    tl = text.lower()
    if any(x in tl for x in ("critical", "triggered", "crashloop", "imagepull", "🔴")):
        return "critical"
    if any(x in tl for x in ("high lat", "acknowledged", "elevated", "error", "failed", "⚠️", "open")):
        return "high"
    if any(x in tl for x in ("in progress", "recently resolved", "medium")):
        return "medium"
    return "info"


def _clip(text: str, n: int = 220) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _issues_from_jira_json(content: str, source: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return issues

    rows = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return issues

    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        key = row.get("key") or ""
        fields = row.get("fields") or {}
        summary = _clip(str(fields.get("summary") or ""), 160)
        status = ""
        st = fields.get("status")
        if isinstance(st, dict):
            status = str(st.get("name") or "")
        elif st:
            status = str(st)
        priority = ""
        pr = fields.get("priority")
        if isinstance(pr, dict):
            priority = str(pr.get("name") or "")

        sev = "high" if status.lower() not in _JIRA_DONE else "info"
        if status.lower() in _JIRA_DONE:
            continue

        issues.append(
            {
                "source": source,
                "severity": sev,
                "service": key,
                "signal": f"Jira {status}".strip(),
                "detail": summary or key,
            }
        )
    return issues


def _issues_from_text(tool_name: str, content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    plain = _strip_html(content)
    if not plain:
        return issues

    for line in plain.split("\n"):
        line = line.strip()
        if len(line) < 8 or not _ISSUE_MARKERS.search(line):
            continue
        if re.search(r"0\s+services?\s+healthy|no\s+(active\s+)?incidents?|no\s+widgets?\s+found", line, re.I):
            continue

        service = ""
        svc_m = _SERVICE_RE.search(line)
        if svc_m:
            service = svc_m.group(1).lower()
        jira_m = _JIRA_KEY_RE.search(line)
        if jira_m and not service:
            service = jira_m.group(1)

        issues.append(
            {
                "source": tool_name,
                "severity": _severity(line),
                "service": service,
                "signal": _clip(line, 120),
                "detail": _clip(line, 200),
            }
        )

    # PagerDuty summary lines (triggered / acknowledged counts)
    for label, pattern in (
        ("PagerDuty active", r"🔴\s*Triggered:\s*(\d+)"),
        ("PagerDuty ack", r"🟡\s*Acknowledged:\s*(\d+)"),
        ("PagerDuty resolved 24h", r"Recently Resolved.*?(\d+)"),
    ):
        m = re.search(pattern, plain, re.I)
        if m and int(m.group(1)) > 0:
            issues.append(
                {
                    "source": tool_name,
                    "severity": "high" if "active" in label or "ack" in label else "medium",
                    "service": "",
                    "signal": label,
                    "detail": f"count={m.group(1)}",
                }
            )

    return issues


def _extract_issues(tool_name: str, content: str) -> list[dict[str, Any]]:
    if not content or not str(content).strip():
        return []

    name_l = (tool_name or "").lower()
    body = str(content)

    if "jira" in name_l or body.strip().startswith("{"):
        jira_issues = _issues_from_jira_json(body, tool_name)
        if jira_issues:
            return jira_issues

    return _issues_from_text(tool_name, body)


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    for item in sorted(issues, key=lambda x: rank.get(x.get("severity", "info"), 9)):
        key = (item.get("source", ""), item.get("service", ""), item.get("detail", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _normalize_alert_key(title: str) -> str:
    """Normalize alert titles for recurrence grouping."""
    t = (title or "").lower()
    t = re.sub(r"#\d+", " ", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}.*", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120] or "unknown"


def _extract_pagerduty_incidents(content: str) -> list[dict[str, Any]]:
    """Parse PagerDuty HTML table rows into structured incidents."""
    incidents: list[dict[str, Any]] = []
    if not content:
        return incidents

    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", content, flags=re.I | re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        if len(cells) < 6:
            continue
        status = _strip_html(cells[0]).upper()
        if status not in {"TRIGGERED", "ACKNOWLEDGED", "RESOLVED"}:
            continue
        num_m = _PD_INCIDENT_NUM_RE.search(cells[1])
        if not num_m:
            continue
        incidents.append(
            {
                "status": status.lower(),
                "number": num_m.group(1),
                "title": _strip_html(cells[2]),
                "service": _strip_html(cells[3]),
                "urgency": _strip_html(cells[4]).lower(),
                "created": _strip_html(cells[5]),
            }
        )
    return incidents


def _extract_jira_tickets(content: str, source: str) -> list[dict[str, Any]]:
    tickets: list[dict[str, Any]] = []
    if content.strip().startswith("{"):
        try:
            data = json.loads(content)
            for row in (data.get("issues") or [])[:40]:
                if not isinstance(row, dict):
                    continue
                fields = row.get("fields") or {}
                status_obj = fields.get("status")
                status = (
                    status_obj.get("name")
                    if isinstance(status_obj, dict)
                    else str(status_obj or "")
                )
                tickets.append(
                    {
                        "key": str(row.get("key") or ""),
                        "summary": str(fields.get("summary") or ""),
                        "status": status,
                        "source": source,
                    }
                )
        except (json.JSONDecodeError, TypeError):
            pass
    return tickets


def _classify_recurrence_pattern(
    total: int,
    active: int,
    resolved: int,
    recently_resolved: int,
    max_cluster: int,
) -> str:
    if total == 0:
        return "INSUFFICIENT_DATA"
    if max_cluster >= 3 or (resolved >= 3 and active >= 1):
        return "CHRONIC_RECURRING"
    if recently_resolved >= 1 and active >= 1:
        return "FLAPPING"
    if max_cluster >= 2 or total >= 2:
        return "RECURRING"
    if total == 1 and active <= 1 and recently_resolved == 0:
        return "ONE_SHOT"
    return "POSSIBLE_RECURRENCE"


def _recurrence_label(pattern: str) -> str:
    return {
        "ONE_SHOT": "Likely one-shot (single occurrence in window)",
        "RECURRING": "Recurring behavior (same alert pattern repeated)",
        "FLAPPING": "Flapping / unstable (resolved recently and active again)",
        "CHRONIC_RECURRING": "Chronic recurrence (persistent repeat pattern)",
        "POSSIBLE_RECURRENCE": "Possible recurrence (limited evidence)",
        "INSUFFICIENT_DATA": "Insufficient data to classify recurrence",
    }.get(pattern, pattern)


def _remediation_for_text(text: str) -> list[str]:
    hints: list[str] = []
    for pattern, hint in _REMEDIATION_HINTS:
        if pattern.search(text or ""):
            hints.append(hint)
    return hints[:3]


def build_recurrence_analysis(
    checkbox_tools: dict[str, str] | None = None,
    mcp_results: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> str:
    """
    Build recurrence + remediation hints block from tool payloads.
    """
    pd_incidents: list[dict[str, Any]] = []
    jira_tickets: list[dict[str, Any]] = []
    recently_resolved_count = 0

    for tool_name, html in (checkbox_tools or {}).items():
        name_l = tool_name.lower()
        body = str(html or "")
        if "pagerduty" in name_l:
            pd_incidents.extend(_extract_pagerduty_incidents(body))
            m = re.search(r"Recently Resolved \(Last 24h\):\s*(\d+)", body, re.I)
            if m:
                recently_resolved_count = max(recently_resolved_count, int(m.group(1)))
        if "jira" in name_l or "atlassian" in name_l:
            jira_tickets.extend(_extract_jira_tickets(body, tool_name))

    for row in mcp_results or []:
        tool_name = str(row.get("tool") or "")
        result = str(row.get("result") or "")
        tl = tool_name.lower()
        if "jira" in tl or "atlassian" in tl:
            jira_tickets.extend(_extract_jira_tickets(result, tool_name))

    lines = ["RECURRENCE & REMEDIATION HINTS (evidence-based):"]

    if pd_incidents:
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for inc in pd_incidents:
            key = _normalize_alert_key(inc.get("title", ""))
            by_key[key].append(inc)

        active = [i for i in pd_incidents if i["status"] in ("triggered", "acknowledged")]
        resolved = [i for i in pd_incidents if i["status"] == "resolved"]
        max_cluster = max((len(v) for v in by_key.values()), default=0)
        top_clusters = sorted(by_key.items(), key=lambda kv: len(kv[1]), reverse=True)[:3]

        overall = _classify_recurrence_pattern(
            len(pd_incidents),
            len(active),
            len(resolved),
            recently_resolved_count,
            max_cluster,
        )
        lines.append(
            f"- PagerDuty window: {len(pd_incidents)} incident(s) | "
            f"active={len(active)} resolved={len(resolved)} "
            f"recently_resolved_24h={recently_resolved_count}"
        )
        lines.append(f"- Overall pattern: {overall} — {_recurrence_label(overall)}")

        for key, cluster in top_clusters:
            if len(cluster) < 1:
                continue
            nums = ", ".join(f"#{c['number']}" for c in cluster[:5])
            statuses = Counter(c["status"] for c in cluster)
            pat = _classify_recurrence_pattern(
                len(cluster),
                statuses.get("triggered", 0) + statuses.get("acknowledged", 0),
                statuses.get("resolved", 0),
                recently_resolved_count if statuses.get("resolved") else 0,
                len(cluster),
            )
            sample_title = cluster[0].get("title", key)
            lines.append(
                f"  • Alert cluster ({len(cluster)}x, {pat}): "
                f"\"{_clip(sample_title, 90)}\" incidents=[{nums}]"
            )
            for hint in _remediation_for_text(sample_title):
                lines.append(f"    → remediation hint: {hint}")

    # Jira duplicate / recurring ticket themes
    open_jira = [t for t in jira_tickets if (t.get("status") or "").lower() not in _JIRA_DONE]
    if open_jira:
        by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in open_jira:
            theme = _normalize_alert_key(t.get("summary", ""))
            by_theme[theme].append(t)
        dup_themes = [(k, v) for k, v in by_theme.items() if len(v) >= 2]
        if dup_themes:
            lines.append("- Jira: similar open tickets suggest recurring operational theme:")
            for theme, group in sorted(dup_themes, key=lambda x: len(x[1]), reverse=True)[:2]:
                keys = ", ".join(g["key"] for g in group[:4])
                lines.append(
                    f"  • {len(group)} ticket(s) [{keys}]: \"{_clip(group[0].get('summary', ''), 80)}\""
                )
        elif len(open_jira) == 1:
            t = open_jira[0]
            lines.append(
                f"- Jira: single open ticket {t.get('key')} — may be one-shot unless PD shows repeats"
            )
            for hint in _remediation_for_text(t.get("summary", "")):
                lines.append(f"  → remediation hint: {hint}")

    # Fallback from extracted issues when PD/Jira parse yielded little
    if len(lines) == 1 and issues:
        blob = " ".join(
            f"{i.get('signal', '')} {i.get('detail', '')}" for i in issues[:8]
        )
        hints = _remediation_for_text(blob)
        if hints:
            lines.append("- Symptom-based remediation hints (from issue text):")
            for h in hints:
                lines.append(f"  → {h}")
        lines.append(
            "- Recurrence: classify as ONE_SHOT vs RECURRING only if evidence shows "
            "repeated incidents/tickets; otherwise state INSUFFICIENT_DATA."
        )

    if len(lines) == 1:
        return (
            "RECURRENCE & REMEDIATION HINTS:\n"
            "- Insufficient incident history in tool output to classify recurrence.\n"
            "- State clearly if one-shot vs recurring cannot be determined from data.\n"
        )

    return "\n".join(lines) + "\n"


    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    for item in sorted(issues, key=lambda x: rank.get(x.get("severity", "info"), 9)):
        key = (item.get("source", ""), item.get("service", ""), item.get("detail", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_issues_context(
    checkbox_tools: dict[str, str] | None = None,
    mcp_results: list[dict[str, Any]] | None = None,
    max_issues: int = _MAX_ISSUES_DEFAULT,
) -> tuple[str, str, str]:
    """
    Build issues-only text for Bedrock + summary + recurrence/remediation block.

    Returns:
        (issues_block, summary_line, recurrence_block)
    """
    collected: list[dict[str, Any]] = []
    tools_with_data = 0

    for tool_name, html in (checkbox_tools or {}).items():
        if not html or _is_tool_error_payload(str(html)):
            continue
        tools_with_data += 1
        collected.extend(_extract_issues(tool_name, str(html)))

    for row in mcp_results or []:
        tool_name = str(row.get("tool") or "MCP")
        result = str(row.get("result") or "")
        if not result.strip():
            continue
        tools_with_data += 1
        if len(result) > _MAX_MCP_JSON_CHARS and result.strip().startswith("{"):
            result = result[:_MAX_MCP_JSON_CHARS] + "\n... (truncated)"
        collected.extend(_extract_issues(tool_name, result))

    issues = _dedupe_issues(collected)[:max_issues]

    critical = sum(1 for i in issues if i.get("severity") == "critical")
    high = sum(1 for i in issues if i.get("severity") == "high")

    if not issues:
        summary = (
            f"Checked {tools_with_data} tool(s); no explicit anomalies extracted "
            "(may be healthy or data format not parsed)."
        )
        recurrence_block = build_recurrence_analysis(
            checkbox_tools, mcp_results, issues
        )
        return "ISSUES: none detected in tool output.\n", summary, recurrence_block

    lines = ["ISSUES (severity order, anomalies only):"]
    for idx, item in enumerate(issues, 1):
        svc = f" | service={item['service']}" if item.get("service") else ""
        lines.append(
            f"{idx}. [{item.get('severity', 'info').upper()}] "
            f"source={item.get('source', '?')}{svc} | "
            f"{item.get('signal', '')} — {_clip(item.get('detail', ''), 180)}"
        )

    summary = (
        f"{len(issues)} issue(s) surfaced ({critical} critical, {high} high); "
        f"healthy services omitted from detail."
    )
    recurrence_block = build_recurrence_analysis(checkbox_tools, mcp_results, issues)
    return "\n".join(lines) + "\n", summary, recurrence_block


def build_bedrock_report_prompt(
    question: str,
    issues_block: str,
    summary_line: str,
    recurrence_block: str = "",
) -> str:
    """Compact triage prompt — anomalies, recurrence pattern, and remediation."""
    recurrence_section = recurrence_block.strip() or (
        "RECURRENCE: insufficient data — state that classification is unknown."
    )
    return f"""You are an SRE triage assistant for Arlo infrastructure (Bedrock Report).

User question: "{question}"

Platform summary (one line): {summary_line}

Evidence — ISSUES ONLY (do not invent data not listed here):
{issues_block}

{recurrence_section}

TASK:
Write a concise HTML incident/triage report focused on what is WRONG, whether it is RECURRING or ONE-SHOT, and actionable remediation.

Rules:
1. Lead with severity: Critical → High → Medium. Max 8 findings; merge duplicates.
2. Do NOT list healthy services or green metrics except ONE short sentence in the hero.
3. Each finding card MUST include:
   - Symptom + evidence (metric, ticket key, incident #, service)
   - Recurrence: ONE_SHOT | RECURRING | FLAPPING | CHRONIC_RECURRING | INSUFFICIENT_DATA
     (use RECURRENCE HINTS above; explain why in one line)
   - Suggested fix / next action (concrete: rollback, scale, check logs, tune alert, etc.)
4. Add a dedicated HTML section titled "🔄 Recurrence Analysis" summarizing:
   - Is this a one-off or repeat pattern?
   - Evidence (incident counts, recently resolved + re-triggered, duplicate Jira themes)
   - Confidence: High / Medium / Low
5. Add a dedicated HTML section titled "🛠️ Recommended Actions" with 3–5 prioritized steps.
6. If ISSUES says none detected, say clearly that nothing degraded was found and what was checked.
7. Do NOT include "Tools Executed", raw JSON dumps, or full ticket tables (Python may add Jira table later).
8. HTML only: start with <div>, end with </div>. Gradient hero + finding cards (color by severity).
9. No markdown code fences.

Return ONLY the HTML."""
