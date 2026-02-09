"""SQS client wrapper for receive/delete operations."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SQSClient:
    """Handles SQS operations."""

    def __init__(self, client: Any):
        self._client = client

    def receive_messages(self, queue_url: str, max_messages: int = 10) -> list[dict]:
        """Receive messages from SQS queue.

        Returns list of raw SQS message dicts (with Body, ReceiptHandle, etc).
        """
        try:
            response = self._client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=5,
            )
            messages = response.get("Messages", [])
            logger.info("Received %d messages from SQS", len(messages))
            return messages
        except Exception as e:
            logger.error("Failed to receive SQS messages: %s", e)
            return []

    def delete_message(self, queue_url: str, receipt_handle: str) -> bool:
        """Delete a message from SQS queue."""
        try:
            self._client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            return True
        except Exception as e:
            logger.error("Failed to delete SQS message: %s", e)
            return False
