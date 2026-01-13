"""SQS publisher service for sending transcription completion messages."""

import json
import logging
import os

from gpu_instance.infrastructure.sqs_client import SQSClient

logger = logging.getLogger(__name__)


class SQSPublisher:
    """Publishes transcription completion messages to SQS."""

    def __init__(self, sqs_client: SQSClient):
        """
        Initialize SQS publisher.

        Args:
            sqs_client: SQS client instance.
        """
        self._sqs_client = sqs_client
        self.queue_url = os.getenv(
            "SQS_FIX_QUEUE_URL",
            "https://sqs.us-east-1.amazonaws.com/707072965202/sqs-fix-transcribes",
        )

    def publish_transcription_complete(
        self,
        timed_key: str | None,
        details: str,
        language: str,
    ) -> bool:
        """
        Publish transcription completion message to SQS.

        Args:
            timed_key: S3 key of the timed transcription file, or None.
            details: Details/description of the transcription.
            language: Language of the transcription.

        Returns:
            True if publish succeeded, False otherwise.
        """
        if not timed_key:
            logger.error("Cannot publish: timed_key is empty or None")
            return False

        try:
            message = {
                "timed_key": timed_key,
                "details": details,
                "language": language,
            }

            logger.info(
                "Publishing transcription complete: %s (%s, %s)",
                timed_key,
                details,
                language,
            )

            return self._sqs_client.send_message(
                queue_url=self.queue_url,
                message_body=json.dumps(message),
            )

        except Exception as e:
            logger.error(
                "Failed to publish transcription complete for %s: %s",
                timed_key,
                e,
                exc_info=True,
            )
            return False
