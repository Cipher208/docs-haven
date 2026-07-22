"""Optional vector search for DocsHaven — TF-IDF based, no external dependencies.

Usage:
    from vector import VectorIndex
    index = VectorIndex(storage)
    index.build()  # Build index from existing documents
    results = index.search("async middleware", limit=5)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING

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

    def build(self, min_df: int = 1) -> None:
        """Build TF-IDF index from all documents in storage."""
        conn = self.storage._get_conn()
        rows = conn.execute("SELECT id, collection, file_path, content, title, chunk_index FROM documents").fetchall()

        if not rows:
            self._built = True
            return

        # Compute document frequency
        df: dict[str, int] = {}
        doc_tokens_list: list[list[str]] = []
        for row in rows:
            tokens = _tokenize(row["content"] + " " + row["title"])
            doc_tokens_list.append(tokens)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                df[t] = df.get(t, 0) + 1

        n_docs = len(rows)
        self._idf = {term: math.log((n_docs + 1) / (freq + 1)) + 1 for term, freq in df.items()}

        # Build document vectors
        self._doc_vectors = []
        for i, row in enumerate(rows):
            tokens = doc_tokens_list[i]
            vector = _tfidf_vector(tokens, self._idf)
            self._doc_vectors.append(
                {
                    "id": row["id"],
                    "collection": row["collection"],
                    "path": f"{row['collection']}/{row['file_path']}",
                    "chunk": row["chunk_index"],
                    "vector": vector,
                    "title": row["title"],
                    "content_preview": row["content"][:200],
                }
            )

        self._built = True

    def search(self, query: str, limit: int = 10, min_score: float = 0.0) -> list[dict]:
        """Search using cosine similarity."""
        if not self._built:
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
