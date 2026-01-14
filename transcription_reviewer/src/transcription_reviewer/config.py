"""Configuration management for the transcription reviewer."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env if exists (local dev only, no-op in Lambda)
load_dotenv()


@dataclass
class Config:
    """Reviewer configuration loaded from environment variables."""

    # AWS
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")

    # S3 Buckets
    transcription_bucket: str = os.getenv(
        "TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription"
    )
    transcription_prefix: str = os.getenv("TRANSCRIPTION_PREFIX", "")

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.transcription_bucket:
            raise ValueError("TRANSCRIPTION_BUCKET environment variable is required")


config = Config()
