"""Document chunking strategies."""

import re
from pathlib import Path

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# File extensions that should use code chunking
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}

# Binary file extensions to skip during indexing
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".whl",
    ".zip",
    ".tar",
    ".gz",
}


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    if chunk_size <= 0:
        return [text]
    if len(text) <= chunk_size:
        return [text]

    # Clamp overlap to prevent infinite loop
    overlap = min(overlap, chunk_size - 1)

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        if end < len(text):
            last_period = chunk.rfind(". ")
            last_newline = chunk.rfind("\n\n")
            break_at = max(last_period, last_newline)
            if break_at > chunk_size // 2:
                chunk = text[start : start + break_at + 1]
                end = start + break_at + 1

        chunks.append(chunk.strip())
        start = end - overlap

    return [c for c in chunks if c]


def chunk_code(text: str) -> list[str]:
    """Chunk code files by function/class boundaries."""
    if not text:
        return []
    # Split on function/class definitions or blank lines
    chunks = re.split(r"\n(?=(?:def |class |async def |# ---|## ))", text)
    return [c.strip() for c in chunks if c.strip()]


def auto_chunk(text: str, file_path: str | None = None) -> list[str]:
    """Auto-select chunking strategy based on file type."""
    if file_path:
        ext = Path(file_path).suffix.lower()
        if ext in _CODE_EXTENSIONS:
            chunks = chunk_code(text)
            if chunks:
                return chunks
    return chunk_text(text)
