"""Anchor tests for query expansion (FUTURE_PLAN P1 #2, DH-2 item 1).

These tests MUST FAIL on current code (Storage has no _expand_query, search()
takes no expand kwarg) and PASS after the query-expansion implementation:
- synonym dictionary (Depends -> dependency injection, dependencies)
- simple stemming (generator -> generate, generating, generated)
- pattern mapping ("how to" -> tutorial, guide, example)
- configurable expansion dictionary (per-call + per-instance)
- expand=True search path recalls synonym-only documents
"""

from __future__ import annotations

from storage import Storage

# ── _expand_query: synonym dictionary ────────────────────────────────────────


def test_expand_query_synonyms(tmp_storage: Storage) -> None:
    """Spec example: Depends -> dependency injection, dependencies."""
    expanded = tmp_storage._expand_query("Depends")
    assert "dependency injection" in expanded, f"synonym missing: {expanded}"
    assert "dependencies" in expanded, f"synonym missing: {expanded}"


def test_expand_query_keeps_original_terms(tmp_storage: Storage) -> None:
    """The original query terms must survive expansion (OR-recall, not replacement)."""
    expanded = tmp_storage._expand_query("Depends parameters")
    assert "depends" in [t.lower() for t in expanded], f"original lost: {expanded}"
    assert "parameters" in [t.lower() for t in expanded], f"original lost: {expanded}"


# ── _expand_query: simple stemming ───────────────────────────────────────────


def test_expand_query_stemming(tmp_storage: Storage) -> None:
    """Spec example: generator -> generate, generating, generated."""
    expanded = tmp_storage._expand_query("generator")
    for form in ("generate", "generating", "generated"):
        assert form in expanded, f"stem variant '{form}' missing: {expanded}"


def test_expand_query_stemming_inflected(tmp_storage: Storage) -> None:
    """Stemming works from inflected forms too: generated -> generate, generator."""
    expanded = tmp_storage._expand_query("generated")
    assert "generate" in expanded, f"stem variant missing: {expanded}"
    assert "generator" in expanded, f"stem variant missing: {expanded}"


# ── _expand_query: pattern mapping ───────────────────────────────────────────


def test_expand_query_pattern_mapping(tmp_storage: Storage) -> None:
    """Spec example: 'how to' -> tutorial, guide, example."""
    expanded = tmp_storage._expand_query("how to use FastAPI")
    for term in ("tutorial", "guide", "example"):
        assert term in expanded, f"pattern term '{term}' missing: {expanded}"


def test_expand_query_pattern_phrase_key(tmp_storage: Storage) -> None:
    """Multi-word dictionary keys map as phrases: 'async generator' -> yield from."""
    expanded = tmp_storage._expand_query("async generator basics")
    assert "yield from" in expanded, f"phrase expansion missing: {expanded}"


# ── _expand_query: configurable expansion dictionary ─────────────────────────


def test_expand_query_configurable_dictionary(tmp_storage: Storage) -> None:
    """Expansion dictionary is configurable: per-call override and per-instance."""
    # Per-call dictionary extends the defaults.
    expanded = tmp_storage._expand_query("deploy", expansion={"deploy": ["deployment", "release"]})
    assert "deployment" in expanded, f"custom expansion missing: {expanded}"
    assert "release" in expanded, f"custom expansion missing: {expanded}"

    # Default dictionary entries still apply alongside custom ones.
    assert "dependencies" in tmp_storage._expand_query("Depends", expansion={"deploy": ["deployment"]})

    # Per-instance dictionary is configurable and takes effect.
    tmp_storage.expansion_dictionary = {"pytest": ["testing", "fixtures"]}
    expanded = tmp_storage._expand_query("pytest")
    assert "testing" in expanded, f"instance expansion missing: {expanded}"
    assert "fixtures" in expanded, f"instance expansion missing: {expanded}"


# ── search(expand=True): expansion reaches the search path ───────────────────


def test_search_expand_recalls_synonym_document(tmp_storage: Storage) -> None:
    """A document that only contains expansion terms must be recalled with expand=True.

    'config' and 'configuration' are not related by the FTS5 porter stemmer
    ('config' vs 'configur'), so only the synonym expansion can match the doc.
    (Note: 'Depends'/'dependency' ARE porter-related and match without expansion.)
    """
    tmp_storage.bulk_insert_raw("cfg", "guide.md", "Guide to configuration management", "Ops Guide")

    plain = tmp_storage.search("config", limit=10)
    assert plain.value == [], f"plain search should not match: {[d['path'] for d in plain.value]}"

    res = tmp_storage.search("config", limit=10, expand=True)
    assert res.is_ok, res
    assert any(d["file_path"] == "guide.md" for d in res.value), f"expansion recall failed: {[d['path'] for d in res.value]}"


def test_search_expand_ranked_by_original_terms(tmp_storage: Storage) -> None:
    """Scoring still ranks by the original query terms: a doc containing the literal
    term outranks a doc matched only via expansion."""
    tmp_storage.bulk_insert_raw("c", "literal.md", "Depends reference manual", "Literal")
    tmp_storage.bulk_insert_raw("c", "synonym.md", "Guide to dependency injection in web frameworks", "Synonym")

    res = tmp_storage.search("Depends", limit=10, expand=True)
    assert res.is_ok, res
    paths = [d["file_path"] for d in res.value]
    assert paths[0] == "literal.md", f"literal match must rank first: {paths}"


def test_search_expand_result_shape_unchanged(tmp_storage: Storage) -> None:
    """expand=True must not change the result dict shape used by existing callers."""
    tmp_storage.bulk_insert_raw("c", "a.md", "Depends and dependencies", "A")
    res = tmp_storage.search("Depends", limit=10, expand=True, explain=True)
    assert res.is_ok, res
    doc = res.value[0]
    for key in ("path", "file_path", "content", "collection", "title", "chunk", "score", "explain"):
        assert key in doc, f"missing key {key}: {doc.keys()}"
