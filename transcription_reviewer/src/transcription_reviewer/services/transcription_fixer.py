"""Service for fixing transcriptions using Bedrock."""

import logging
from pathlib import Path

from transcription_reviewer.infrastructure.bedrock_client import BedrockClient
from transcription_reviewer.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)

TEMPLATE_BUCKET = "portal-daf-yomi-audio"


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

    def fix_transcription(self, content: str, transcription_key: str) -> str | None:
        """
        Fix a transcription using Bedrock.

        Args:
            content: Raw transcription content.
            transcription_key: S3 key of the transcription file.

        Returns:
            Fixed transcription, or None if failed.
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

        if result:
            logger.info("Transcription fixed, output length: %d chars", len(result))
        else:
            logger.error("Failed to fix transcription")

        return result
