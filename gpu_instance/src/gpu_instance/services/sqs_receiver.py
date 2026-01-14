"""SQS receiver service for polling messages."""

import json
import logging
import os

from dotenv import load_dotenv

from gpu_instance.infrastructure.sqs_client import SQSClient
from gpu_instance.models.schemas import SQSMessage

load_dotenv()
logger = logging.getLogger(__name__)


class SQSReceiver:
    """Handles receiving messages from SQS queue."""

    def __init__(self, sqs_client: SQSClient):
        """
        Initialize SQS receiver.

        Args:
            sqs_client: SQSClient instance.
        """
        self._sqs_client = sqs_client
        self._queue_url = os.getenv("SQS_QUEUE_URL", "")

    @property
    def queue_url(self) -> str:
        """Get the SQS queue URL."""
        return self._queue_url

    def receive_messages(
        self,
        max_messages: int = 1,
        wait_time: int = 20,
    ) -> list[SQSMessage]:
        """
        Receive messages from SQS queue.

        Args:
            max_messages: Maximum number of messages to receive.
            wait_time: Long polling wait time in seconds.

        Returns:
            List of SQSMessage objects.
        """
        if not self._queue_url:
            logger.error("SQS_QUEUE_URL not set in environment")
            return []

        raw_messages = self._sqs_client.receive_messages(
            queue_url=self._queue_url,
            max_messages=max_messages,
            wait_time=wait_time,
        )

        messages = []
        for raw in raw_messages:
            try:
                body = json.loads(raw.get("Body", "{}"))
                message = SQSMessage(
                    s3_key=body.get("s3_key", ""),
                    language=body.get("language", ""),
                    details=body.get("details", ""),
                    receipt_handle=raw.get("ReceiptHandle"),
                )
                messages.append(message)
            except Exception as e:
                logger.error("Failed to parse SQS message: %s", e)
                continue

        return messages

    def delete_message(self, message: SQSMessage) -> bool:
        """
        Delete a message from SQS queue.

        Args:
            message: SQSMessage to delete.

        Returns:
            True if deletion succeeded, False otherwise.
        """
        if not self._queue_url:
            logger.error("SQS_QUEUE_URL not set in environment")
            return False

        if not message.receipt_handle:
            logger.error("Message has no receipt handle")
            return False

        return self._sqs_client.delete_message(
            queue_url=self._queue_url,
            receipt_handle=message.receipt_handle,
        )
