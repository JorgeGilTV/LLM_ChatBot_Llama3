"""Jira query parsing and MintMCP Atlassian Rovo helpers."""
from __future__ import annotations

import json
import re
from typing import Any

JIRA_SEARCH_TOOL = "atlassian-rovo__searchJiraIssuesUsingJql"
JIRA_GET_ISSUE_TOOL = "atlassian-rovo__getJiraIssue"
ATLASSIAN_RESOURCES_TOOL = "atlassian-rovo__getAccessibleAtlassianResources"

_JIRA_KEYWORDS = (
    "jira",
    "ticket",
    "tickets",
    "issue",
    "issues",
    "epic",
    "story",
    "bug",
    "incidencia",
    "incidente",
    "tiquete",
    "tiquetes",
)

_STATUS_JQL = {
    "open": 'status in ("Open", "New", "To Do")',
    "in progress": 'status in ("In Progress", "In Development")',
    "closed": 'status in ("Closed", "Done", "Resolved")',
    "resolved": 'status in ("Resolved", "Done")',
}

_PROJECT_STOPWORDS = frozenset(
    {
        "DE", "DEL", "LA", "LAS", "LOS", "EL", "EN", "FOR", "THE", "AND", "OR",
        "MUESTRA", "MUESTRAME", "SHOW", "LIST", "ALL", "TODOS", "TODAS",
    }
)


def is_jira_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    if re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", question.upper()):
        return True
    ql = question.lower()
    return any(kw in ql for kw in _JIRA_KEYWORDS)


def build_jira_jql_from_question(question: str) -> str | None:
    """Build JQL from natural language (Spanish/English)."""
    raw = (question or "").strip()
    if not raw:
        return None

    ticket_ids = re.findall(r"\b([A-Z][A-Z0-9]+-\d+)\b", raw.upper())
    if ticket_ids:
        if len(ticket_ids) == 1:
            return f"key = {ticket_ids[0]}"
        return " OR ".join(f"key = {t}" for t in ticket_ids)

    if not is_jira_question(raw):
        return None

    ql = raw.lower()
    clauses: list[str] = []

    for status, jql in _STATUS_JQL.items():
        if status in ql or any(s in ql for s in status.split()):
            clauses.append(jql)
            break

    project = None
    for pattern in (
        r"\btickets?\s+(?:de|del|for|in|en)\s+([A-Z][A-Z0-9]+)\b",
        r"\bissues?\s+(?:de|del|for|in|en)\s+([A-Z][A-Z0-9]+)\b",
        r"\b(?:project|proyecto)\s+([A-Z][A-Z0-9]+)\b",
        r"\b([A-Z]{2,10})\s+tickets?\b",
    ):
        m = re.search(pattern, raw, re.I)
        if m:
            candidate = m.group(1).upper()
            if candidate not in _PROJECT_STOPWORDS:
                project = candidate
                break

    if project:
        clauses.append(f'project = "{project}"')

    text_terms: list[str] = []
    after_project = re.search(
        rf"\b{re.escape(project)}\b\s+(?:de|del|for|about|sobre)?\s*([A-Za-z0-9][A-Za-z0-9_-]*)",
        raw,
        re.I,
    ) if project else None
    if after_project:
        term = after_project.group(1).strip()
        if term.upper() not in _PROJECT_STOPWORDS and len(term) >= 2:
            text_terms.append(term)

    if not text_terms:
        m = re.search(r"\b(?:about|sobre|mentioning|con)\s+([A-Za-z0-9][A-Za-z0-9_-]+)", raw, re.I)
        if m:
            text_terms.append(m.group(1))

    for term in text_terms:
        clauses.append(f'text ~ "{term}"')

    if not clauses:
        terms = [
            w
            for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", raw)
            if w.lower() not in _JIRA_KEYWORDS
            and w.upper() not in _PROJECT_STOPWORDS
            and w.lower() not in {"muestrame", "muestra", "show", "list", "please", "los", "las"}
        ]
        if terms:
            clauses.append(f'text ~ "{terms[-1]}"')

    if not clauses:
        return None

    return f"{' AND '.join(clauses)} ORDER BY updated DESC"


async def resolve_atlassian_cloud_id(session) -> str | None:
    """Resolve Arlo Atlassian cloudId (cached on session when possible)."""
    cached = getattr(session, "_arlo_atlassian_cloud_id", None)
    if cached:
        return cached

    try:
        result = await session.call_tool(ATLASSIAN_RESOURCES_TOOL, {})
        text = ""
        if hasattr(result, "content"):
            for item in result.content:
                if hasattr(item, "text"):
                    text += item.text
        resources = json.loads(text) if text.strip().startswith("[") else []
        for res in resources:
            url = (res.get("url") or "").lower()
            if "arlo.atlassian.net" in url and res.get("id"):
                cloud_id = str(res["id"])
                setattr(session, "_arlo_atlassian_cloud_id", cloud_id)
                return cloud_id
        if resources and resources[0].get("id"):
            cloud_id = str(resources[0]["id"])
            setattr(session, "_arlo_atlassian_cloud_id", cloud_id)
            return cloud_id
    except Exception:
        pass
    return None


async def run_jira_mcp_search(session, question: str, max_results: int = 25) -> dict[str, Any] | None:
    """
    Run MintMCP Jira search when the question looks Jira-related.
    Returns {tool, result, jql} or None.
    """
    jql = build_jira_jql_from_question(question)
    if not jql:
        return None

    ticket_ids = re.findall(r"\b([A-Z][A-Z0-9]+-\d+)\b", question.upper())
    if len(ticket_ids) == 1:
        cloud_id = await resolve_atlassian_cloud_id(session)
        if not cloud_id:
            return None
        try:
            result = await session.call_tool(
                JIRA_GET_ISSUE_TOOL,
                {"cloudId": cloud_id, "issueIdOrKey": ticket_ids[0]},
            )
            text = ""
            if hasattr(result, "content"):
                for item in result.content:
                    if hasattr(item, "text"):
                        text += item.text
            if text.strip():
                return {"tool": JIRA_GET_ISSUE_TOOL, "result": text, "jql": jql}
        except Exception:
            pass

    cloud_id = await resolve_atlassian_cloud_id(session)
    if not cloud_id:
        return None

    try:
        result = await session.call_tool(
            JIRA_SEARCH_TOOL,
            {"cloudId": cloud_id, "jql": jql, "maxResults": max_results},
        )
        text = ""
        if hasattr(result, "content"):
            for item in result.content:
                if hasattr(item, "text"):
                    text += item.text
        if text.strip():
            return {"tool": JIRA_SEARCH_TOOL, "result": text, "jql": jql}
    except Exception:
        return None
    return None
