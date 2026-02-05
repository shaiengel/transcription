"""Token counting service using Anthropic SDK."""

import logging

from anthropic import AnthropicBedrock

logger = logging.getLogger(__name__)


class TokenCounter:
    """Counts tokens using Anthropic's count_tokens API via Bedrock."""

    def __init__(self, model_id: str, region: str = "us-east-1"):
        """Initialize TokenCounter.

        Args:
            model_id: Anthropic model ID (e.g., "claude-opus-4-5-20251101")
            region: AWS region for Bedrock
        """
        self.client = AnthropicBedrock(aws_region=region)
        # Strip the "us." prefix if present (Bedrock uses prefixed IDs, but count_tokens needs base ID)
        self.model_id = model_id.replace("us.", "").replace("-v1:0", "")

    def count_content_tokens(self, content: str) -> int:
        """Count tokens for content only (excludes system prompt).

        Used to estimate output tokens since output = corrected content only.

        Args:
            content: The transcription content

        Returns:
            Number of tokens in content
        """
        try:
            response = self.client.messages.count_tokens(
                model=self.model_id,
                messages=[{"role": "user", "content": content}],
            )
            return response.input_tokens
        except Exception as e:
            logger.warning("Token counting failed, using word estimate: %s", e)
            # Fallback to word-based estimation (4x for Hebrew text)
            word_count = len(content.split())
            return int(word_count * 4)
