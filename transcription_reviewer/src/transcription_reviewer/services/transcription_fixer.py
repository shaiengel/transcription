"""Service for fixing transcriptions using Bedrock."""

import logging
from pathlib import Path

from transcription_reviewer.infrastructure.bedrock_client import BedrockClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.utils.vtt_converter import convert_to_vtt, has_valid_timeline

logger = logging.getLogger(__name__)

TEMPLATE_BUCKET = "portal-daf-yomi-audio"
OUTPUT_BUCKET = "final-transcription"


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

        'prefix/output1.timed.txt' -> 'output1.template.txt'
        """
        path = Path(transcription_key)
        stem = path.stem.replace(".timed", "")
        return f"{stem}.template.txt"

    def _get_vtt_key(self, transcription_key: str) -> str:
        """Get VTT output key from transcription key.

        'prefix/output1.timed.txt' -> 'output1.vtt'
        """
        path = Path(transcription_key)
        stem = path.stem.replace(".timed", "")
        return f"{stem}.vtt"

    def _get_stem(self, transcription_key: str) -> str:
        """Get stem from transcription key.

        'prefix/output1.timed.txt' -> 'output1'
        """
        path = Path(transcription_key)
        return path.stem.replace(".timed", "")

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

        Args:
            content: Raw transcription content.
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

        # Check if Bedrock preserved the timeline format
        if has_valid_timeline(result):
            logger.info("Bedrock output has valid timeline, using fixed content")
            vtt_content = convert_to_vtt(result)
        else:
            logger.warning("Bedrock changed timeline format, using original content")
            vtt_content = convert_to_vtt(content)

        # Upload VTT to output bucket
        vtt_key = self._get_vtt_key(transcription_key)
        if self._s3_client.put_object_content(OUTPUT_BUCKET, vtt_key, vtt_content):
            logger.info("Saved VTT to s3://%s/%s", OUTPUT_BUCKET, vtt_key)
        else:
            logger.error("Failed to save VTT to s3://%s/%s", OUTPUT_BUCKET, vtt_key)
            return None

        return vtt_content
