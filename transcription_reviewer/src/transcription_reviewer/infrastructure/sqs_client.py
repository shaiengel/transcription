"""SQS client wrapper for AWS operations."""

import json
import logging
from typing import Any

from botocore.exceptions import ClientError

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

    def send_message(self, queue_url: str, message_body: dict) -> bool:
        """
        Send a message to SQS queue.

        Args:
            queue_url: SQS queue URL.
            message_body: Message payload as dict.

        Returns:
            True if successful, False otherwise.
        """
        try:
            response = self._client.send_message(
                QueueUrl=queue_url, MessageBody=json.dumps(message_body)
            )
            logger.info(f"Sent message to {queue_url}: {response['MessageId']}")
            return True
        except ClientError as e:
            logger.error(f"Failed to send message to {queue_url}: {e}")
            return False
