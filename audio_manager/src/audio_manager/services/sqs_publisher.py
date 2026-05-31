import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from audio_manager.infrastructure.sqs_client import SQSClient

env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path, override=True)
logger = logging.getLogger(__name__)


class SQSPublisher:
    """Publishes messages to SQS queue."""

    def __init__(self, sqs_client: SQSClient):
        self._sqs_client = sqs_client
        self._queue_url = os.getenv("SQS_QUEUE_URL")

    def publish_upload(self, media_id: int) -> bool:
        """Publish upload notification to SQS."""
        if not self._queue_url:
            logger.error("SQS_QUEUE_URL not set in environment")
            return False

        message = {"media_id": media_id}
        try:
            return self._sqs_client.send_message(self._queue_url, message)
        except Exception as e:
            logger.error("Failed to publish to SQS: %s", e)
            return False
