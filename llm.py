"""NL question pipeline for kb_ask + LLM access through the MCP host.

DocsHaven never calls an external LLM API itself (zero-dependency principle):
summaries are produced by the MCP host via sampling/createMessage. When the host
cannot provide completions, callers degrade gracefully (answer_mode
"host_unavailable") instead of failing.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import SamplingMessage, TextContent

from scoring import _query_terms

logger = logging.getLogger(__name__)

MAX_ANSWER_TOKENS = 1024

ANSWER_SYSTEM_PROMPT = (
    "You answer questions strictly from the numbered knowledge-base sources provided. "
    "Cite sources inline as [1], [2] matching their numbers. "
    "If the sources do not contain the answer, say so plainly."
)

# Question words and filler that carry no retrieval signal.
QUESTION_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "best", "but", "by", "can", "could", "did", "do", "does",
        "explain", "find", "for", "from", "get", "got", "how", "i", "if", "in", "is", "it", "its", "know", "may",
        "me", "might", "my", "need", "of", "on", "or", "should", "show", "tell", "that", "the", "there", "this",
        "to", "use", "used", "using", "want", "was", "way", "ways", "we", "were", "what", "when", "where", "which",
        "who", "why", "will", "with", "would", "you",
    }
)


def extract_keywords(question: str) -> list[str]:
    """Extract searchable keywords from a natural-language question.

    "how to use Depends with parameters in FastAPI?" -> ["depends", "parameters", "fastapi"]
    """
    return [t for t in _query_terms(question) if t not in QUESTION_STOPWORDS]


def build_answer_prompt(question: str, chunks: list[dict]) -> str:
    """Assemble the sampling prompt: the question plus numbered sources [n]."""
    lines = [f"Question: {question}", "", "Sources:"]
    for i, chunk in enumerate(chunks, start=1):
        title = chunk.get("title") or chunk.get("file_path")
        lines.append(f"[{i}] {title} ({chunk['collection']}/{chunk['file_path']} #{chunk['chunk_index']})")
        lines.append(str(chunk.get("content", "")))
        lines.append("")
    lines.append("Answer the question using only the sources above. Cite inline as [n].")
    return "\n".join(lines)


async def host_complete(
    ctx: Any,
    prompt: str,
    *,
    system_prompt: str = ANSWER_SYSTEM_PROMPT,
    max_tokens: int = MAX_ANSWER_TOKENS,
) -> str | None:
    """Ask the MCP host for a completion via sampling/createMessage.

    Returns the completion text, or None when the host cannot provide one
    (no request context, host without sampling support, or any failure).
    """
    if ctx is None:
        return None
    try:
        session = ctx.session
        result = await session.create_message(
            messages=[SamplingMessage(role="user", content=TextContent(type="text", text=prompt))],
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
    except Exception as e:
        # Graceful degradation: any host-side failure (unsupported sampling,
        # no client capabilities, transport error) means "no completion".
        logger.info("Host LLM unavailable (%s): %s", type(e).__name__, e)
        return None

    content = getattr(result, "content", None)
    text = getattr(content, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    if isinstance(content, list):
        parts = [getattr(c, "text", "") for c in content if getattr(c, "type", None) == "text"]
        joined = "\n".join(p for p in parts if p).strip()
        return joined or None
    return None
