import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SQSClient:
    """Handles SQS operations."""

    def __init__(self, client: Any):
        self._client = client

    def send_message(self, queue_url: str, message: dict) -> bool:
        """Send a message to SQS queue."""
        try:
            self._client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message),
            )
            logger.info("Sent message to SQS: %s", queue_url)
            return True
        except Exception as e:
            logger.error("Failed to send SQS message: %s", e)
            return False
