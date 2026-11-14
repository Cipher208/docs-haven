"""Pinning tests for PS3 scoring: score > 0, explain nonzero, min_score filters, importance_boost reflected.

These tests MUST FAIL on the current code (score always 0.0, explain zeros, importance_boost 0.0 because
retrieval_count/created_at are not selected in FTS/LIKE queries) and PASS after the PS3 fix.
"""

from __future__ import annotations

from storage import Storage


def test_score_positive_and_ranked(tmp_storage: Storage) -> None:
    """Поиск возвращает score > 0 для документов, и порядок ranked по importance (retrieval_count)."""
    storage = tmp_storage

    # Три документа с одинаковым содержимым (bm25_norm будет одинаковым), но разным retrieval_count.
    storage.bulk_insert_raw("c", "a.md", "unique_keyword_alpha body", "A")
    storage.bulk_insert_raw("c", "b.md", "unique_keyword_alpha body", "B")
    storage.bulk_insert_raw("c", "c.md", "unique_keyword_alpha body", "C")

    conn = storage._get_conn()
    conn.execute("UPDATE documents SET retrieval_count = 5 WHERE file_path = 'b.md' AND collection = 'c'")
    conn.execute("UPDATE documents SET retrieval_count = 20 WHERE file_path = 'c.md' AND collection = 'c'")
    conn.commit()

    result = storage.search("unique_keyword_alpha", collections=["c"], limit=10, explain=True)
    assert result.is_ok, result
    docs = result.value
    assert len(docs) == 3, [(d["path"], d.get("score")) for d in docs]

    # Каждый документ должен иметь score > 0 (после исправления importance_boost > 0 для всех).
    for d in docs:
        assert d.get("score", 0) > 0, f"score for {d['path']} is {d.get('score')} (explain={d.get('explain')})"

    # Порядок должен быть: c (retrieval_count=20) > b (5) > a (0), потому что bm25_norm одинаковый,
    # а importance_boost разный.
    paths = [d["path"] for d in docs]
    assert paths[0] == "c/c.md", f"expected c/c.md first, got {paths}"
    assert paths[1] == "c/b.md", f"expected c/b.md second, got {paths}"
    assert paths[2] == "c/a.md", f"expected c/a.md third, got {paths}"


def test_explain_shows_nonzero_components(tmp_storage: Storage) -> None:
    """explain возвращает ненулевые component scores (base_score и importance_boost)."""
    storage = tmp_storage

    storage.bulk_insert_raw("c", "x.md", "alpha body text here", "X")

    result = storage.search("alpha", collections=["c"], limit=10, explain=True)
    assert result.is_ok, result
    doc = result.value[0]
    explain = doc.get("explain")
    assert explain is not None, f"no explain in {doc}"

    # importance_boost должен быть > 0 (recency, потому что created_at есть у всех документов).
    assert explain.get("importance_boost", 0) > 0, f"importance_boost is {explain.get('importance_boost')} (explain={explain})"
    # final_score должен быть > 0 (bm25_norm может быть 0.0 для единственного документа, но imp_boost > 0).
    assert explain.get("final_score", 0) > 0, f"final_score is {explain.get('final_score')} (explain={explain})"


def test_min_score_filters(tmp_storage: Storage) -> None:
    """min_score фильтрует результаты.

    При query "shared" lexical_relevance = 0.65 (токен + фраза в content), importance_boost
    (recency) ~0.1 → score ~0.75. Первый поиск поднимает retrieval_count, поэтому второй
    поиск с min_score чуть выше 0.75 должен отфильтровать оба документа.
    """
    storage = tmp_storage

    storage.bulk_insert_raw("c", "a.md", "shared text", "A")
    storage.bulk_insert_raw("c", "b.md", "shared text", "B")

    # Первый поиск: score ~ 0.75 >= 0.05 → оба проходят.
    result = storage.search("shared", collections=["c"], limit=10, min_score=0.05)
    assert result.is_ok, result
    docs = result.value
    assert len(docs) == 2, f"expected 2 docs with min_score=0.05, got {len(docs)}: {[(d['path'], round(d.get('score'), 4)) for d in docs]}"

    # Второй поиск: retrieval_count уже 1 → score ~ 0.7575, min_score 0.77 фильтрует.
    result = storage.search("shared", collections=["c"], limit=10, min_score=0.77)
    assert result.is_ok, result
    # fmt: skip keeps this message on one line: ruff format's wrap would split
    # the three result.value accesses across lines and un-deduplicate mypy's
    # report of the same pre-existing union-attr finding (BH-6: mypy content
    # must stay identical to HEAD).
    assert len(result.value) == 0, f"expected 0 docs with min_score=0.77, got {len(result.value)}: {[(d['path'], round(d.get('score'), 4)) for d in result.value]}"  # fmt: skip


def test_importance_boost_reflected(tmp_storage: Storage) -> None:
    """importance_boost reflects retrieval_count: документ с большим retrieval_count имеет больший importance_boost."""
    storage = tmp_storage

    storage.bulk_insert_raw("c", "x.md", "alpha body text here", "X")
    storage.bulk_insert_raw("c", "y.md", "alpha body text here", "Y")

    conn = storage._get_conn()
    conn.execute("UPDATE documents SET retrieval_count = 100 WHERE file_path = 'x.md' AND collection = 'c'")
    conn.commit()

    result = storage.search("alpha", collections=["c"], limit=10, explain=True)
    assert result.is_ok, result
    docs = result.value
    assert len(docs) == 2, docs

    doc_x = next(d for d in docs if d["path"] == "c/x.md")
    doc_y = next(d for d in docs if d["path"] == "c/y.md")

    importance_x = doc_x["explain"]["importance_boost"]
    importance_y = doc_y["explain"]["importance_boost"]
    assert importance_x > importance_y, (
        f"importance_x={importance_x} <= importance_y={importance_y} (explain_x={doc_x['explain']}, explain_y={doc_y['explain']})"
    )
