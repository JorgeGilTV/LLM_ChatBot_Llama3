"""Extract APM / PagerDuty service names from free-text user queries."""
from __future__ import annotations

import re

_SERVICE_TOKEN_RE = re.compile(
    r"\b(backend-[a-z0-9][a-z0-9-]*|[a-z][a-z0-9]*-[a-z0-9][a-z0-9-]*)\b",
    re.I,
)

_BARE_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$", re.I)

# "with/for/about" only — avoid bare "on" ("going on with …" would capture "with").
_AFTER_PREP_RE = re.compile(
    r"\b(?:with|for|about|regarding)\s+['\"]?([a-z0-9][a-z0-9_-]*)['\"]?\b",
    re.I,
)

_NOISE_TOKENS = frozenset(
    {
        "the", "all", "any", "some", "this", "that", "what", "going", "wrong",
        "status", "errors", "error", "incidents", "incident", "metrics", "show",
        "tell", "give", "happening", "wrong", "please", "check", "look", "into",
        "zones", "regions", "region", "zone", "everything", "services", "service",
        "with", "for", "about", "on", "regarding", "is", "are", "was", "were",
    }
)


def _normalize_token(token: str) -> str:
    return token.lower().strip("?.,!\"'")


def extract_service_name_from_query(text: str) -> str:
    """
    Pull a service token from natural language or return a bare service name unchanged.

    Examples:
        "what is going on with hmsautomation-scheduler?" -> hmsautomation-scheduler
        "what is going on with samsung?" -> samsung
        "what's happening with hmsguard?" -> hmsguard
        "backend-hmspayment errors" -> backend-hmspayment
        "hmsguard" -> hmsguard
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    if _BARE_SERVICE_RE.fullmatch(raw) and len(raw) >= 3:
        return raw.lower()

    matches = _SERVICE_TOKEN_RE.findall(raw)
    if matches:
        return max(matches, key=lambda m: (m.count("-"), len(m))).lower()

    candidates: list[str] = []
    for m in _AFTER_PREP_RE.finditer(raw):
        token = _normalize_token(m.group(1))
        if token and token not in _NOISE_TOKENS and len(token) >= 3:
            candidates.append(token)

    if candidates:
        # Prefer the last match ("… going on with samsung" → samsung).
        return candidates[-1]

    return ""
