"""Service for fixing transcriptions using Bedrock."""

import logging
import os
import re
from pathlib import Path

from transcription_reviewer.infrastructure.bedrock_client import BedrockClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.utils.vtt_converter import convert_to_vtt

logger = logging.getLogger(__name__)

TEMPLATE_BUCKET = os.getenv("TEMPLATE_BUCKET", "portal-daf-yomi-audio")
OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET", "final-transcription")
TRANSCRIPTION_BUCKET = os.getenv("TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")

# Pattern to match timed lines: [1] 00:00:00.000 - 00:00:03.300: text
TIMED_LINE_PATTERN = re.compile(
    r"^(\[\d+\]\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+-\s+\d{2}:\d{2}:\d{2}\.\d{3}:\s*)(.*)$"
)


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

class TranscriptionFixer:
    """Fixes transcriptions using Bedrock Claude."""

    def __init__(self, bedrock_client: BedrockClient, s3_client: S3Client, model_id: str):
        """
        Initialize transcription fixer.

        Args:
            bedrock_client: BedrockClient instance.
            s3_client: S3Client instance for reading templates.
            model_id: Bedrock model ID to use.
        """
        self._bedrock_client = bedrock_client
        self._s3_client = s3_client
        self._model_id = model_id

    def _get_template_key(self, transcription_key: str) -> str:
        """Extract stem and build template key.

        'prefix/output1.txt' -> 'output1.template.txt'
        """
        path = Path(transcription_key)
        stem = path.stem
        return f"{stem}.template.txt"

    def _get_vtt_key(self, transcription_key: str) -> str:
        """Get VTT output key from transcription key.

        'prefix/output1.txt' -> 'output1.vtt'
        """
        path = Path(transcription_key)
        stem = path.stem
        return f"{stem}.vtt"

    def _get_stem(self, transcription_key: str) -> str:
        """Get stem from transcription key.

        'prefix/output1.txt' -> 'output1'
        """
        path = Path(transcription_key)
        return path.stem

    def _get_time_file_key(self, transcription_key: str) -> str:
        """Get the .time file key from transcription key.

        'prefix/output1.txt' -> 'prefix/output1.time'
        """
        path = Path(transcription_key)
        stem = path.stem
        return f"{stem}.time"

    def get_system_prompt(self, transcription_key: str) -> str | None:
        """Get system prompt for a transcription.

        Args:
            transcription_key: S3 key of the transcription file.

        Returns:
            System prompt content, or None if failed.
        """
        template_key = self._get_template_key(transcription_key)
        return self._s3_client.get_object_content(TEMPLATE_BUCKET, template_key)

    def fix_transcription(self, content: str, transcription_key: str) -> str | None:
        """
        Fix a transcription using Bedrock and save as VTT.

        Workflow:
        1. Send plain text content to Bedrock
        2. Read the .time file with timestamps from S3
        3. Inject timestamps into Bedrock's result
        4. If line counts don't match, use original .time file for VTT

        Args:
            content: Plain text transcription content (without timestamps).
            transcription_key: S3 key of the transcription file.

        Returns:
            VTT content, or None if failed.
        """
        # Read system prompt from S3 template
        template_key = self._get_template_key(transcription_key)
        system_prompt = self._s3_client.get_object_content(TEMPLATE_BUCKET, template_key)

        if not system_prompt:
            logger.error("Failed to read template: s3://%s/%s", TEMPLATE_BUCKET, template_key)
            return None

        logger.info("Fixing transcription, input length: %d chars", len(content))

        result = self._bedrock_client.invoke_model(
            model_id=self._model_id,
            system_prompt=system_prompt,
            user_message=content,
        )

        if not result:
            logger.error("Failed to fix transcription")
            return None

        logger.info("Transcription fixed, output length: %d chars", len(result))

        # Read the .time file with timestamps
        time_file_key = self._get_time_file_key(transcription_key)
        timed_content = self._s3_client.get_object_content(TRANSCRIPTION_BUCKET, time_file_key)

        if not timed_content:
            logger.error("Failed to read time file: s3://%s/%s", TRANSCRIPTION_BUCKET, time_file_key)
            return None

        # Try to inject timestamps back into the fixed text
        timed_result = inject_timestamps(result, timed_content)

        if timed_result:
            logger.info("Timestamps injected successfully")
            vtt_content = convert_to_vtt(timed_result)
            txt_content = result
        else:
            logger.warning("Line count mismatch, using original .time file for VTT")
            vtt_content = convert_to_vtt(timed_content)
            txt_content = result

        stem = self._get_stem(transcription_key)

        # Upload TXT (Bedrock result with timestamps) to output bucket
        txt_key = f"{stem}.txt"
        if self._s3_client.put_object_content(OUTPUT_BUCKET, txt_key, txt_content):
            logger.info("Saved TXT to s3://%s/%s", OUTPUT_BUCKET, txt_key)
        else:
            logger.error("Failed to save TXT to s3://%s/%s", OUTPUT_BUCKET, txt_key)
            return None

        # Upload VTT to output bucket
        vtt_key = f"{stem}.vtt"
        if self._s3_client.put_object_content(OUTPUT_BUCKET, vtt_key, vtt_content):
            logger.info("Saved VTT to s3://%s/%s", OUTPUT_BUCKET, vtt_key)
        else:
            logger.error("Failed to save VTT to s3://%s/%s", OUTPUT_BUCKET, vtt_key)
            return None

        return vtt_content
