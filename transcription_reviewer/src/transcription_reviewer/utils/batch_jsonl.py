"""Utility for preparing Bedrock batch inference JSONL files."""

import json
from dataclasses import dataclass
from pathlib import Path

from transcription_reviewer.models.schemas import TranscriptionFile

MIN_ENTRIES = 100
SMALL_FILE_THRESHOLD = 30  # lines
SPLIT_CHUNK_SIZE = 35  # lines (30-40 range)


@dataclass
class BatchEntry:
    """A single entry for batch processing."""

    record_id: str
    system_prompt: str
    content: str


def split_content(content: str, chunk_size: int = SPLIT_CHUNK_SIZE) -> list[str]:
    """Split content into chunks of approximately chunk_size lines.

    Does not split in the middle of a line.
    """
    lines = content.strip().split("\n")
    chunks = []
    for i in range(0, len(lines), chunk_size):
        chunk = "\n".join(lines[i : i + chunk_size])
        chunks.append(chunk)
    return chunks


def prepare_batch_entries(files: list[TranscriptionFile]) -> list[BatchEntry]:
    """
    Prepare batch entries from transcription files.

    Algorithm:
    1. Separate small files (< 30 lines) from big files
    2. Add all small files as entries
    3. If < 100 entries, split big files until we reach 100
    """
    entries: list[BatchEntry] = []

    # Separate small and big files
    small_files = [f for f in files if f.line_count < SMALL_FILE_THRESHOLD]
    big_files = [f for f in files if f.line_count >= SMALL_FILE_THRESHOLD]

    # Add all small files as single entries
    for f in small_files:
        entries.append(
            BatchEntry(
                record_id=f.stem,
                system_prompt=f.system_prompt,
                content=f.content,
            )
        )

    # If we have enough entries, return
    if len(entries) >= MIN_ENTRIES:
        return entries

    # Split big files until we reach MIN_ENTRIES
    for f in big_files:
        if len(entries) >= MIN_ENTRIES:
            break

        chunks = split_content(f.content)
        for i, chunk in enumerate(chunks, start=1):
            entries.append(
                BatchEntry(
                    record_id=f"{f.stem}_{i}",
                    system_prompt=f.system_prompt,
                    content=chunk,
                )
            )
            if len(entries) >= MIN_ENTRIES:
                break

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
