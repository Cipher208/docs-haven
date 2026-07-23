"""Optional vector search for DocsHaven — TF-IDF based, no external dependencies.

Usage:
    from vector import VectorIndex
    index = VectorIndex(storage)
    index.build()  # Build index from existing documents
    results = index.search("async middleware", limit=5)
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from storage import Storage


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector from tokens."""
    tf = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {term: (count / total) * idf.get(term, 0.0) for term, count in tf.items() if idf.get(term, 0) > 0}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorIndex:
    """TF-IDF vector index for semantic search without external dependencies.

    Optional: only used when strategy='vector' or strategy='hybrid'.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._idf: dict[str, float] = {}
        self._doc_vectors: list[dict] = []  # [{id, collection, path, vector, content_preview}]
        self._built = False

    def build(self, min_df: int = 1, batch_size: int = 1000) -> None:
        """Build TF-IDF index from all documents in storage."""
        conn = self.storage._get_conn()

        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        if total == 0:
            self._built = True
            return

        # Phase 1: compute document frequencies in batches
        df: dict[str, int] = {}
        doc_tokens_list: list[list[str]] = []

        for offset in range(0, total, batch_size):
            rows = conn.execute(
                "SELECT id, collection, file_path, content, title, chunk_index "
                "FROM documents LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            for row in rows:
                tokens = _tokenize(row["content"] + " " + row["title"])
                doc_tokens_list.append(tokens)
                for t in set(tokens):
                    df[t] = df.get(t, 0) + 1

        n_docs = len(doc_tokens_list)
        self._idf = {term: math.log((n_docs + 1) / (freq + 1)) + 1 for term, freq in df.items()}

        # Phase 2: build document vectors in batches
        self._doc_vectors = []
        idx = 0
        for offset in range(0, total, batch_size):
            rows = conn.execute(
                "SELECT id, collection, file_path, content, title, chunk_index "
                "FROM documents LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            for row in rows:
                if idx < len(doc_tokens_list):
                    tokens = doc_tokens_list[idx]
                    vector = _tfidf_vector(tokens, self._idf)
                    self._doc_vectors.append({
                        "id": row["id"],
                        "collection": row["collection"],
                        "path": f"{row['collection']}/{row['file_path']}",
                        "chunk": row["chunk_index"],
                        "vector": vector,
                        "title": row["title"],
                        "content_preview": row["content"][:200],
                    })
                    idx += 1

        self._built = True

    def search(self, query: str, limit: int = 10, min_score: float = 0.0) -> list[dict]:
        """Search using cosine similarity."""
        if not self._built:
            conn = self.storage._get_conn()
            count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if count > 50_000:
                logger.warning("VectorIndex skipped: %d docs exceeds 50k limit", count)
                return []
            self.build()

        if not self._doc_vectors:
            return []

        query_tokens = _tokenize(query)
        query_vector = _tfidf_vector(query_tokens, self._idf)

        if not query_vector:
            return []

        scored = []
        for doc in self._doc_vectors:
            score = _cosine_similarity(query_vector, doc["vector"])
            if score >= min_score:
                scored.append(
                    {
                        "path": doc["path"],
                        "collection": doc["collection"],
                        "title": doc["title"],
                        "content": doc["content_preview"],
                        "score": round(score, 4),
                        "source": "vector",
                    }
                )

        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    def rebuild(self) -> None:
        """Force rebuild of the index."""
        self._built = False
        self.build()
