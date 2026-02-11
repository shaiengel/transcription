"""SQS sender service for publishing completion messages."""

import json
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from gpu_timestamp.infrastructure.sqs_client import SQSClient

load_dotenv()
logger = logging.getLogger(__name__)


class SQSSender:
    """Handles sending messages to SQS queue."""

    def __init__(self, sqs_client: SQSClient):
        """
        Initialize SQS sender.

        Args:
            sqs_client: SQSClient instance.
        """
        self._sqs_client = sqs_client
        self._queue_url = os.getenv("SQS_FINAL_QUEUE_URL", "")

    @property
    def queue_url(self) -> str:
        """Get the SQS queue URL."""
        return self._queue_url

    def send_completion_message(
        self,
        stem: str,
        source_audio: str,
        vtt_key: str,
        json_key: str,
    ) -> bool:
        """
        Send a completion message to the final queue.

        Args:
            stem: Base filename (without extension).
            source_audio: Original audio file S3 key.
            vtt_key: Output VTT file S3 key.
            json_key: Output JSON file S3 key.

        Returns:
            True if send succeeded, False otherwise.
        """
        if not self._queue_url:
            logger.warning("SQS_FINAL_QUEUE_URL not set, skipping notification")
            return False

        message = {
            "stem": stem,
            "source_audio": source_audio,
            "vtt_key": vtt_key,
            "json_key": json_key,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._sqs_client.send_message(
            queue_url=self._queue_url,
            message_body=json.dumps(message),
        )
