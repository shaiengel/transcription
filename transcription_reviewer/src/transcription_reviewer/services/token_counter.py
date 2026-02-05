"""Token counting service using local tiktoken tokenizer."""

import logging

import tiktoken

# from anthropic import AnthropicBedrock  # Commented out - Bedrock doesn't support count_tokens yet

logger = logging.getLogger(__name__)


class TokenCounter:
    """Counts tokens using local tiktoken tokenizer (offline, no API calls)."""

    def __init__(self, model_id: str, region: str = "us-east-1"):
        """Initialize TokenCounter with local tokenizer.

        Args:
            model_id: Anthropic model ID (e.g., "us.anthropic.claude-opus-4-5-20251101-v1:0")
            region: AWS region (kept for interface compatibility, not used for token counting)
        """
        # Use cl100k_base encoding (similar to Claude/GPT-4 tokenization)
        # This is a local operation - no API calls
        self.encoding = tiktoken.get_encoding("cl100k_base")

        # Store model_id for logging/debugging
        self.model_id = model_id.replace("us.", "").replace("-v1:0", "")

        # # Original Bedrock implementation (commented out - not supported yet)
        # self.client = AnthropicBedrock(aws_region=region)
        # # Strip the "us." prefix if present (Bedrock uses prefixed IDs, but count_tokens needs base ID)
        # self.model_id = model_id.replace("us.", "").replace("-v1:0", "")

    def count_content_tokens(self, content: str) -> int:
        """Count tokens for content only (excludes system prompt).

        Used to estimate output tokens since output = corrected content only.
        Uses local tiktoken tokenizer (offline, no API calls).

        Args:
            content: The transcription content

        Returns:
            Number of tokens in content
        """
        try:
            # Use local tiktoken encoding - fast and free
            tokens = self.encoding.encode(content)
            return len(tokens)

            # # Original Bedrock API implementation (commented out - not supported yet)
            # response = self.client.messages.count_tokens(
            #     model=self.model_id,
            #     messages=[{"role": "user", "content": content}],
            # )
            # return response.input_tokens

        except Exception as e:
            logger.warning("Token counting failed, using word estimate: %s", e)
            # Fallback to word-based estimation (4x for Hebrew text)
            word_count = len(content.split())
            return int(word_count * 4)
