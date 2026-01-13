"""SQS client wrapper for AWS operations."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SQSClient:
    """Handles SQS operations."""

    def __init__(self, client: Any):
        """
        Initialize SQS client wrapper.

        Args:
            client: boto3 SQS client instance.
        """
        self._client = client

    def receive_messages(
        self,
        queue_url: str,
        max_messages: int = 1,
        wait_time: int = 20,
        visibility_timeout: int = 600,
    ) -> list[dict]:
        """
        Receive messages from SQS queue.

        Args:
            queue_url: SQS queue URL.
            max_messages: Maximum number of messages to receive.
            wait_time: Long polling wait time in seconds.
            visibility_timeout: Visibility timeout in seconds.

        Returns:
            List of message dictionaries.
        """
        try:
            response = self._client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time,
                VisibilityTimeout=visibility_timeout,
            )
            messages = response.get("Messages", [])
            if messages:
                logger.info("Received %d message(s) from SQS", len(messages))
            return messages
        except Exception as e:
            logger.error("Failed to receive messages from SQS: %s", e)
            return []

    def delete_message(self, queue_url: str, receipt_handle: str) -> bool:
        """
        Delete a message from SQS queue.

        Args:
            queue_url: SQS queue URL.
            receipt_handle: Message receipt handle.

        Returns:
            True if deletion succeeded, False otherwise.
        """
        try:
            self._client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            logger.info("Deleted message from SQS")
            return True
        except Exception as e:
            logger.error("Failed to delete message from SQS: %s", e)
            return False
