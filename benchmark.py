"""Benchmark script for DocsHaven — measure search performance.

Uses generated test documents with Lorem ipsum filler text for realistic
search benchmarking. Not shipped to end users (excluded from package via
pyproject.toml tool.hatch.build.exclude).
"""

import logging
import time
from pathlib import Path

from storage import Storage

logger = logging.getLogger(__name__)


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


def _create_benchmark_storage() -> Storage:
    """Create temporary storage for benchmarking."""
    import tempfile

    d = tempfile.mkdtemp()
    return Storage(Path(d))


def _populate_benchmark_docs(storage: Storage, n_docs: int = 1000) -> None:
    """Populate storage with benchmark documents."""
    docs = generate_docs(n_docs)
    for name, content in docs.items():
        storage.bulk_insert_raw("benchmark", name, content, f"Document {Path(name).stem}")


def benchmark_indexing(storage: Storage, n_docs: int) -> float:
    """Benchmark document indexing speed."""
    docs = generate_docs(n_docs)

    start = time.perf_counter()
    for name, content in docs.items():
        storage.bulk_insert_raw("benchmark", name, content, f"Document {Path(name).stem}")
    elapsed = time.perf_counter() - start

    return elapsed


def _warmup(storage: Storage, queries: list[str], iterations: int = 10) -> None:
    """Warmup search to initialize FTS5 cache."""
    for _ in range(iterations):
        for q in queries:
            storage.search(q, limit=10)


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

    _warmup(storage, queries)

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
    logger.info("=" * 60)
    logger.info("DocsHaven Benchmark")
    logger.info("=" * 60)

    storage = _create_benchmark_storage()
    try:
        _populate_benchmark_docs(storage, 1000)

        for n in [100, 500, 1000]:
            elapsed = benchmark_indexing(storage, n)
            rate = n / elapsed
            logger.info("Index %5d docs: %.3fs (%.0f docs/sec)", n, elapsed, rate)

        stats = benchmark_search(storage, n_queries=100)
        logger.info("")
        logger.info("--- Search Benchmark (1000 docs, %d queries) ---", stats["total_queries"])
        logger.info("  Total queries: %d", stats["total_queries"])
        logger.info("  Total time:    %.2fms", stats["total_time_ms"])
        logger.info("  Avg per query: %.3fms", stats["avg_ms"])
        logger.info("  Min:           %.3fms", stats["min_ms"])
        logger.info("  Max:           %.3fms", stats["max_ms"])
        logger.info("  P95:           %.3fms", stats["p95_ms"])
        logger.info("  Throughput:    %d queries/sec", stats["queries_per_second"])
    finally:
        storage.close()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Benchmark complete!")
    logger.info("=" * 60)


def run_benchmark_json() -> dict:
    """Run benchmark and return results as dict."""

    results: dict = {}
    storage = _create_benchmark_storage()
    try:
        for n in [100, 500, 1000]:
            elapsed = benchmark_indexing(storage, n)
            results[f"index_{n}"] = {
                "docs": n,
                "elapsed_s": round(elapsed, 3),
                "docs_per_sec": round(n / elapsed),
            }

        results["search"] = benchmark_search(storage, n_queries=100)
    finally:
        storage.close()

    return results


if __name__ == "__main__":
    import json
    import sys

    if "--json" in sys.argv:
        results = run_benchmark_json()
        print(json.dumps(results, indent=2))
    else:
        run_benchmark()
