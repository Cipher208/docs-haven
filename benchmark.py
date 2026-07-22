"""Benchmark script for DocsHaven — measure search performance.

Uses generated test documents with Lorem ipsum filler text for realistic
search benchmarking. Not shipped to end users (excluded from package via
pyproject.toml tool.hatch.build.exclude).
"""

import tempfile
import time
from pathlib import Path

from storage import Storage


def generate_docs(n: int) -> dict[str, str]:
    """Generate n test documents."""
    docs: dict[str, str] = {}
    for i in range(n):
        docs[f"doc_{i:04d}.md"] = f"""# Document {i}

This is document number {i} in the benchmark suite.

## Section 1

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam.

## Section 2

Key terms: fastapi, dependency injection, async, middleware, authentication,
database, sql, sqlalchemy, pydantic, validation, error handling, testing.

Topic {i % 10}: This document covers aspect {i} of the knowledge base.

## Summary

Document {i} provides information about topic group {i % 5}.
"""
    return docs


def benchmark_indexing(storage: Storage, n_docs: int) -> float:
    """Benchmark document indexing speed."""
    docs = generate_docs(n_docs)

    start = time.perf_counter()
    conn = storage._get_conn()
    for name, content in docs.items():
        conn.execute(
            "INSERT OR REPLACE INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("benchmark", name, content, f"Document {name.split('.')[0]}"),
        )
    conn.commit()
    # Don't close conn — it's Storage's persistent connection
    elapsed = time.perf_counter() - start

    return elapsed


def benchmark_search(storage: Storage, n_queries: int = 100) -> dict:
    """Benchmark search speed."""
    queries = [
        "fastapi dependency injection",
        "async middleware authentication",
        "database sql sqlalchemy",
        "pydantic validation error",
        "testing pytest fixtures",
        "async middleware",
        "dependency injection",
        "authentication",
        "database",
        "validation",
    ]

    times: list[float] = []

    # Warmup: 10 queries to initialize FTS5 cache
    for _ in range(10):
        for q in queries:
            storage.search(q, limit=10)

    for _ in range(n_queries):
        for q in queries:
            start = time.perf_counter()
            storage.search(q, limit=10)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

    return {
        "total_queries": len(times),
        "total_time_ms": round(sum(times) * 1000, 2),
        "avg_ms": round(sum(times) / len(times) * 1000, 3),
        "min_ms": round(min(times) * 1000, 3),
        "max_ms": round(max(times) * 1000, 3),
        "p95_ms": round(sorted(times)[int(len(times) * 0.95)] * 1000, 3),
        "queries_per_second": round(len(times) / sum(times)),
    }


def run_benchmark() -> None:
    """Run full benchmark suite."""
    print("=" * 60)
    print("DocsHaven Benchmark")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))

        # Benchmark indexing
        for n in [100, 500, 1000]:
            elapsed = benchmark_indexing(storage, n)
            rate = n / elapsed
            print(f"\nIndex {n:>5} docs: {elapsed:.3f}s ({rate:.0f} docs/sec)")

        # Benchmark search
        print("\n--- Search Benchmark (1000 docs, 1000 queries) ---")
        stats = benchmark_search(storage, n_queries=100)
        print(f"  Total queries: {stats['total_queries']}")
        print(f"  Total time:    {stats['total_time_ms']}ms")
        print(f"  Avg per query: {stats['avg_ms']}ms")
        print(f"  Min:           {stats['min_ms']}ms")
        print(f"  Max:           {stats['max_ms']}ms")
        print(f"  P95:           {stats['p95_ms']}ms")
        print(f"  Throughput:    {stats['queries_per_second']} queries/sec")

        storage.close()

        print("\n" + "=" * 60)
        print("Benchmark complete!")
        print("=" * 60)


if __name__ == "__main__":
    run_benchmark()
