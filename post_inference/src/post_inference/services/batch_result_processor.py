"""Service for parsing batch output and processing results."""

import json
import logging
import re
from collections import defaultdict

from post_inference.infrastructure.s3_client import S3Client
from post_inference.models.schemas import BatchOutputRecord
from post_inference.utils.vtt_converter import convert_to_vtt

logger = logging.getLogger(__name__)

# Pattern to match timed lines: [1] 00:00:00.000 - 00:00:03.300: text
TIMED_LINE_PATTERN = re.compile(
    r"^(\[\d+\]\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+-\s+\d{2}:\d{2}:\d{2}\.\d{3}:\s*)(.*)$"
)

# Pattern to detect split record IDs like "12345_2"
SPLIT_PATTERN = re.compile(r"^(.+)_(\d+)$")


def inject_timestamps(fixed_text: str, timed_content: str) -> str | None:
    """Inject timestamps from timed_content into fixed_text.

    Args:
        fixed_text: Plain text from Bedrock (one line per original line).
        timed_content: Original content with timestamps.

    Returns:
        Content with timestamps injected, or None if line counts don't match.
    """
    fixed_lines = [line.strip() for line in fixed_text.strip().split("\n") if line.strip()]
    timed_lines = [line.strip() for line in timed_content.strip().split("\n") if line.strip()]

    if len(fixed_lines) != len(timed_lines):
        logger.warning(
            "Line count mismatch: fixed=%d, original=%d",
            len(fixed_lines),
            len(timed_lines),
        )
        return None

    result_lines = []
    for fixed_line, timed_line in zip(fixed_lines, timed_lines):
        match = TIMED_LINE_PATTERN.match(timed_line)
        if match:
            timestamp_prefix = match.group(1)
            result_lines.append(f"{timestamp_prefix}{fixed_line}")
        else:
            result_lines.append(fixed_line)

    return "\n".join(result_lines)


def parse_batch_output(content: str) -> list[BatchOutputRecord]:
    """Parse Bedrock batch output JSONL into records.

    Skips dummy records (recordId starting with "dummy_").
    """
    records = []

    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSONL line: %s", line[:100])
            continue

        record_id = data.get("recordId", "")
        if record_id.startswith("dummy_"):
            continue

        model_output = data.get("modelOutput", {})
        content_list = model_output.get("content", [])

        if not content_list:
            logger.warning("No content in model output for record: %s", record_id)
            continue

        fixed_text = content_list[0].get("text", "")
        if not fixed_text:
            logger.warning("Empty text in model output for record: %s", record_id)
            continue

        records.append(BatchOutputRecord(record_id=record_id, fixed_text=fixed_text))

    logger.info("Parsed %d real records from batch output", len(records))
    return records


def group_split_records(records: list[BatchOutputRecord]) -> dict[str, str]:
    """Group split records by base stem and merge their text.

    Records like "12345_1", "12345_2" are merged into "12345".
    Single records like "12345" are kept as-is.

    Returns:
        Dict mapping stem -> merged fixed text.
    """
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)

    for record in records:
        match = SPLIT_PATTERN.match(record.record_id)
        if match:
            base_stem = match.group(1)
            chunk_idx = int(match.group(2))
            groups[base_stem].append((chunk_idx, record.fixed_text))
        else:
            groups[record.record_id].append((0, record.fixed_text))

    merged: dict[str, str] = {}
    for stem, chunks in groups.items():
        chunks.sort(key=lambda x: x[0])
        merged[stem] = "\n".join(text for _, text in chunks)

    return merged


class BatchResultProcessor:
    """Processes batch inference results into VTT files."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def process_record(
        self,
        stem: str,
        fixed_text: str,
        transcription_bucket: str,
        output_bucket: str,
    ) -> bool:
        """Process a single record: match .time file, create VTT, upload.

        Args:
            stem: File stem (e.g., "12345").
            fixed_text: Merged fixed text from Bedrock.
            transcription_bucket: Bucket containing .time files.
            output_bucket: Bucket for output .vtt files.

        Returns:
            True if successful, False if error.
        """
        # Read .time file
        time_key = f"{stem}.time"
        timed_content = self._s3_client.get_object_content(transcription_bucket, time_key)

        if not timed_content:
            logger.error("Failed to read time file: s3://%s/%s", transcription_bucket, time_key)
            return False

        # Inject timestamps
        timed_result = inject_timestamps(fixed_text, timed_content)

        # Upload TXT (LLM-fixed text for RAG)
        txt_key = f"{stem}.txt"
        if not self._s3_client.put_object_content(output_bucket, txt_key, fixed_text):
            logger.error("Failed to save TXT to s3://%s/%s", output_bucket, txt_key)
            return False

        if timed_result:
            logger.info("Timestamps injected successfully for %s", stem)
            vtt_content = convert_to_vtt(timed_result)
        else:
            logger.warning("Line count mismatch for %s, using original .time file for VTT", stem)
            vtt_content = convert_to_vtt(timed_content)

            no_timing_key = f"{stem}.no_timing.txt"
            if not self._s3_client.put_object_content(output_bucket, no_timing_key, fixed_text):
                logger.error("Failed to save no_timing to s3://%s/%s", output_bucket, no_timing_key)
                return False
            logger.info("Saved no_timing to s3://%s/%s", output_bucket, no_timing_key)

        # Upload VTT
        vtt_key = f"{stem}.vtt"
        if not self._s3_client.put_object_content(output_bucket, vtt_key, vtt_content):
            logger.error("Failed to save VTT to s3://%s/%s", output_bucket, vtt_key)
            return False

        logger.info("Saved VTT to s3://%s/%s", output_bucket, vtt_key)
        return True
