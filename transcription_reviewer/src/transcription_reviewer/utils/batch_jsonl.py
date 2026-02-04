"""Utility for preparing Bedrock batch inference JSONL files."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from transcription_reviewer.models.schemas import TranscriptionFile

MIN_ENTRIES = int(os.getenv("MIN_ENTRIES", "100"))
MIN_WORDS_TO_SPLIT = int(os.getenv("MIN_WORDS_TO_SPLIT", "4000"))
CHUNK_TARGET_WORDS = int(os.getenv("CHUNK_TARGET_WORDS", "4000"))
MIN_REMAINDER_WORDS = int(os.getenv("MIN_REMAINDER_WORDS", "3000"))


@dataclass
class BatchEntry:
    """A single entry for batch processing."""

    record_id: str
    system_prompt: str
    content: str


def split_content_by_words(content: str) -> list[str]:
    """Split content into chunks based on word count.

    - Only splits if content has >= MIN_WORDS_TO_SPLIT words
    - Each chunk targets CHUNK_TARGET_WORDS words
    - Won't split if remainder would be < MIN_REMAINDER_WORDS
    - Splits only at line boundaries
    """
    lines = content.strip().split("\n")
    total_words = len(content.split())

    # Don't split small files
    if total_words < MIN_WORDS_TO_SPLIT:
        return [content.strip()]

    chunks = []
    current_chunk_lines: list[str] = []
    current_word_count = 0
    remaining_words = total_words

    for line in lines:
        line_words = len(line.split())
        current_chunk_lines.append(line)
        current_word_count += line_words
        remaining_words -= line_words

        # Check if we should split here
        if current_word_count >= CHUNK_TARGET_WORDS:
            # Would the remainder be too small?
            if remaining_words >= MIN_REMAINDER_WORDS:
                # Make the split
                chunks.append("\n".join(current_chunk_lines))
                current_chunk_lines = []
                current_word_count = 0
            # else: don't split, continue accumulating

    # Add any remaining lines as final chunk
    if current_chunk_lines:
        chunks.append("\n".join(current_chunk_lines))

    return chunks


def prepare_batch_entries(files: list[TranscriptionFile]) -> list[BatchEntry]:
    """
    Prepare batch entries from transcription files.

    Algorithm:
    1. If file count >= MIN_ENTRIES, use all files as-is (no splitting needed)
    2. Otherwise, separate small files (< MIN_WORDS_TO_SPLIT words) from big files
    3. Add all small files as single entries
    4. Split big files based on word count thresholds
    """
    # If we already have enough files, no need to split anything
    if len(files) >= MIN_ENTRIES:
        return [
            BatchEntry(
                record_id=f.stem,
                system_prompt=f.system_prompt,
                content=f.content,
            )
            for f in files
        ]

    entries: list[BatchEntry] = []

    # Separate small and big files based on word count
    small_files = [f for f in files if f.word_count < MIN_WORDS_TO_SPLIT]
    big_files = [f for f in files if f.word_count >= MIN_WORDS_TO_SPLIT]

    # Add all small files as single entries
    for f in small_files:
        entries.append(
            BatchEntry(
                record_id=f.stem,
                system_prompt=f.system_prompt,
                content=f.content,
            )
        )

    # Split big files based on word count
    for f in big_files:
        chunks = split_content_by_words(f.content)
        for i, chunk in enumerate(chunks, start=1):
            record_id = f.stem if len(chunks) == 1 else f"{f.stem}_{i}"
            entries.append(
                BatchEntry(
                    record_id=record_id,
                    system_prompt=f.system_prompt,
                    content=chunk,
                )
            )

    return entries


def create_jsonl(entries: list[BatchEntry], output_path: Path) -> bool:
    """Create JSONL file from batch entries.

    Returns False if there are fewer than MIN_ENTRIES entries.
    """
    if len(entries) < MIN_ENTRIES:
        return False

    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            record = {
                "recordId": entry.record_id,
                "modelInput": {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 64000,
                    "temperature": 0.1,
                    "system": entry.system_prompt,
                    "messages": [{"role": "user", "content": entry.content}],
                },
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return True
