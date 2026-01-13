import logging
import os

from dotenv import load_dotenv

from audio_manager.infrastructure.sqs_client import SQSClient

logger = logging.getLogger(__name__)


class SQSPublisher:
    """Publishes messages to SQS queue."""

    def __init__(self, sqs_client: SQSClient):
        self._sqs_client = sqs_client
        load_dotenv()
        self._queue_url = os.getenv("SQS_QUEUE_URL")

    def publish_upload(
        self,
        s3_key: str,
        language: str | None,
        massechet_name: str,
        daf_name: str,
    ) -> bool:
        """Publish upload notification to SQS."""
        if not self._queue_url:
            logger.error("SQS_QUEUE_URL not set in environment")
            return False

        message = {
            "s3_key": s3_key,
            "language": language or "unknown",
            "massechet_name": massechet_name,
            "daf_name": daf_name,
        }
        try:
            return self._sqs_client.send_message(self._queue_url, message)
        except Exception as e:
            logger.error("Failed to publish to SQS: %s", e)
            return False
