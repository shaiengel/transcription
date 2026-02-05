"""Utility for preparing Bedrock batch inference JSONL files."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_reviewer.models.schemas import TranscriptionFile

if TYPE_CHECKING:
    from transcription_reviewer.services.token_counter import TokenCounter

logger = logging.getLogger(__name__)

MIN_ENTRIES = int(os.getenv("MIN_ENTRIES", "100"))
# Max tokens before Bedrock cuts off output
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "60000"))
# Temperature for phonetic correction (0.3-0.5 helps with sound-alike reasoning)
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.4"))


@dataclass
class BatchEntry:
    """A single entry for batch processing."""

    record_id: str
    system_prompt: str
    content: str
    token_count: int = 0


def _split_content_by_tokens(
    content: str, total_tokens: int, max_tokens: int
) -> list[tuple[str, int]]:
    """Split content into chunks based on token count.

    Uses proportional estimation: tokens_per_line = total_tokens / total_lines

    Args:
        content: The full content to split
        total_tokens: Actual token count for the full content
        max_tokens: Maximum tokens per chunk

    Returns:
        List of (chunk_content, estimated_tokens) tuples
    """
    lines = content.strip().split("\n")
    total_lines = len(lines)

    if total_lines == 0:
        return [(content.strip(), total_tokens)]

    # Don't split if already under limit
    if total_tokens <= max_tokens:
        return [(content.strip(), total_tokens)]

    # Estimate tokens per line for proportional splitting
    tokens_per_line = total_tokens / total_lines

    chunks: list[tuple[str, int]] = []
    current_chunk_lines: list[str] = []
    current_estimated_tokens = 0.0

    for line in lines:
        line_tokens = tokens_per_line  # Each line gets proportional tokens

        # Would adding this line exceed the limit?
        if current_estimated_tokens + line_tokens > max_tokens and current_chunk_lines:
            # Save current chunk and start new one
            chunks.append(("\n".join(current_chunk_lines), int(current_estimated_tokens)))
            current_chunk_lines = [line]
            current_estimated_tokens = line_tokens
        else:
            current_chunk_lines.append(line)
            current_estimated_tokens += line_tokens

    # Add any remaining lines as final chunk
    if current_chunk_lines:
        chunks.append(("\n".join(current_chunk_lines), int(current_estimated_tokens)))

    return chunks


def _create_dummy_entry(index: int) -> BatchEntry:
    """Create a minimal dummy entry to pad the batch."""
    return BatchEntry(
        record_id=f"dummy_{index}",
        system_prompt="ok",
        content="ok",
        token_count=2,
    )


def prepare_batch_entries(
    files: list[TranscriptionFile], token_counter: TokenCounter
) -> list[BatchEntry]:
    """
    Prepare batch entries from transcription files.

    Algorithm:
    1. For each file, count tokens via API (once per file)
    2. If tokens > MAX_TOKENS, split by lines proportionally
    3. Always pad to MIN_ENTRIES with dummy entries for batch pricing

    Args:
        files: List of transcription files to process
        token_counter: TokenCounter service for counting tokens

    Returns:
        List of BatchEntry objects (padded to MIN_ENTRIES)
    """
    entries: list[BatchEntry] = []

    for f in files:
        # Count content tokens only (system prompt doesn't appear in output)
        total_tokens = token_counter.count_content_tokens(f.content)
        logger.info(
            "File %s: %d tokens (limit: %d)",
            f.stem,
            total_tokens,
            MAX_TOKENS,
        )

        # Split if over the limit
        chunks = _split_content_by_tokens(f.content, total_tokens, MAX_TOKENS)

        if len(chunks) > 1:
            logger.info("Split %s into %d chunks", f.stem, len(chunks))

        for i, (chunk_content, chunk_tokens) in enumerate(chunks, start=1):
            record_id = f.stem if len(chunks) == 1 else f"{f.stem}_{i}"

            entries.append(
                BatchEntry(
                    record_id=record_id,
                    system_prompt=f.system_prompt,
                    content=chunk_content,
                    token_count=chunk_tokens,
                )
            )

    # Always pad to MIN_ENTRIES with dummy entries
    real_count = len(entries)
    for i in range(real_count, MIN_ENTRIES):
        entries.append(_create_dummy_entry(i))

    return entries


def create_jsonl(entries: list[BatchEntry], output_path: Path) -> dict:
    """Create JSONL file from batch entries.

    Returns stats about the created batch.
    """
    real_entries = [e for e in entries if not e.record_id.startswith("dummy_")]
    dummy_entries = [e for e in entries if e.record_id.startswith("dummy_")]

    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            record = {
                "recordId": entry.record_id,
                "modelInput": {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                    "system": entry.system_prompt,
                    "messages": [{"role": "user", "content": entry.content}],
                },
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "total_entries": len(entries),
        "real_entries": len(real_entries),
        "dummy_entries": len(dummy_entries),
        "total_tokens": sum(e.token_count for e in entries),
        "real_tokens": sum(e.token_count for e in real_entries),
    }
