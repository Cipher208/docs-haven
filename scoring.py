"""Search result scoring signals."""

import math
import time

_SECONDS_PER_DAY = 86400

TYPE_KEYWORDS = {
    "api": ["api", "endpoint", "route", "handler", "request", "response"],
    "tutorial": ["tutorial", "guide", "howto", "how to", "step", "example"],
    "reference": ["reference", "docs", "documentation", "spec", "specification"],
    "config": ["config", "configuration", "setup", "install", "environment"],
}

RECENCY_WEIGHT = 0.1
FREQUENCY_WEIGHT = 0.05
AGE_HALF_LIFE_DAYS = 90


def type_boost(query: str, result: dict) -> float:
    query_lower = query.lower()
    title_lower = result.get("title", "").lower()
    content_lower = result.get("content", "").lower()[:200]

    for keywords in TYPE_KEYWORDS.values():
        if not any(kw in query_lower for kw in keywords):
            continue
        if any(k in title_lower or k in content_lower for k in keywords):
            return 0.15
    return 0.0


def importance_boost(result: dict, now: float | None = None) -> float:
    if now is None:
        now = time.time()
    boost = 0.0
    created_at = result.get("created_at")
    if created_at:
        try:
            from datetime import datetime

            created_ts = datetime.fromisoformat(created_at).timestamp()
            age_days = (now - created_ts) / _SECONDS_PER_DAY
            recency = math.exp(-0.693 * age_days / AGE_HALF_LIFE_DAYS)
            boost += RECENCY_WEIGHT * recency
        except (ValueError, TypeError):
            pass
    retrieval_count = result.get("retrieval_count", 0)
    if retrieval_count > 0:
        freq = min(1.0, math.log10(retrieval_count + 1) / 2)
        boost += FREQUENCY_WEIGHT * freq
    return round(boost, 4)
