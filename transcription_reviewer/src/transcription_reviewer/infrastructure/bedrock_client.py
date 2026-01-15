"""Bedrock client wrapper for AWS operations."""

import json
import logging
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class BedrockClient:
    """Handles Bedrock operations."""

    def __init__(self, client: Any):
        """
        Initialize Bedrock client wrapper.

        Args:
            client: boto3 bedrock-runtime client instance.
        """
        self._client = client

    def invoke_model(
        self,
        model_id: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 64000,
    ) -> str | None:
        """
        Invoke a Bedrock model with a message.

        Args:
            model_id: Bedrock model ID.
            system_prompt: System prompt for the model.
            user_message: User message content.
            max_tokens: Maximum tokens in response.

        Returns:
            Model response text, or None if failed.
        """
        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": user_message,
                    }
                ],
            }

            logger.info("Invoking Bedrock model: %s", model_id)
            response = self._client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
            )

            result = json.loads(response["body"].read())
            text = result["content"][0]["text"]
            logger.info("Bedrock response received, length: %d chars", len(text))
            return text

        except ClientError as e:
            logger.error("Failed to invoke Bedrock model: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error invoking Bedrock: %s", e)
            return None
