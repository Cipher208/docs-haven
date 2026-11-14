"""Search result scoring signals."""

import math
import re
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

# Lexical relevance weights (corpus-independent, in [0,1]).
W_COVERAGE = 0.6
W_TITLE = 0.3
W_PHRASE = 0.1

# The phrase bonus scans title/content for the query terms joined as one phrase.
# It only pays off for short queries (kb_search and kb_learn cap their term sets at
# 24 via _query_terms); callers with large term sets (kb_related passes ALL unique
# document terms) skip the scan — its substring can never match and would cost
# O(content) per candidate.
_PHRASE_SCAN_MAX_TERMS = 24

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_RESERVED = {"and", "or", "not", "near"}


def _query_terms(text: str, limit: int = 24) -> list[str]:
    """Normalize a query into deduplicated, lowercased tokens, drop stopwords."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in _TOKEN_RE.findall((text or "").casefold()):
        if len(tok) < 2 or tok in _RESERVED or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _token_set(text: str) -> set[str]:
    """Tokenize a document title/content for lexical matching."""
    return set(_TOKEN_RE.findall((text or "").casefold())) - _RESERVED


def lexical_relevance(query_terms: list[str], title: str, content: str, bm25_score: float | None = None) -> float:
    """Corpus-independent relevance in [0,1] from token coverage, title hit, and phrase hit.

    query_terms must be pre-tokenized by _query_terms. bm25_score, if provided, is used only
    as a tiny tie-break so FTS ranking can still nudge identical-lexical results.

    The phrase bonus is a substring scan and is bounded: term sets above
    _PHRASE_SCAN_MAX_TERMS (e.g. kb_related's all-unique-terms source set) skip
    it — coverage and title signals are phrase-independent.
    """
    qt = set(query_terms)
    if not qt:
        return 0.0

    title_tokens = _token_set(title)
    content_tokens = _token_set(content)
    hits = qt & (title_tokens | content_tokens)

    coverage = len(hits) / len(qt)
    title_cov = len(qt & title_tokens) / len(qt)

    phrase = 0.0
    if len(query_terms) <= _PHRASE_SCAN_MAX_TERMS:
        query_phrase = " ".join(query_terms)
        if query_phrase and query_phrase in (title or "").casefold():
            phrase = 1.0
        elif query_phrase and query_phrase in (content or "").casefold():
            phrase = 0.5

    base = W_COVERAGE * coverage + W_TITLE * title_cov + W_PHRASE * phrase

    if bm25_score is not None:
        # Tie-break only: on tiny corpora bm25 floors at ~1e-6 and is negligible.
        base += 0.01 * min(1.0, abs(bm25_score) / (abs(bm25_score) + 1e-3))

    return max(0.0, min(1.0, base))


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
