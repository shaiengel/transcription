"""Configuration management for the timestamp alignment worker."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)


@dataclass
class Config:
    """Worker configuration loaded from environment variables."""

    # AWS
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")

    # S3 Buckets
    audio_bucket: str = os.getenv("AUDIO_BUCKET", "portal-daf-yomi-audio")
    text_bucket: str = os.getenv("TEXT_BUCKET", "final-transcription")
    output_bucket: str = os.getenv("OUTPUT_BUCKET", "final-transcription")

    # SQS
    sqs_queue_url: str = os.getenv("SQS_QUEUE_URL", "")
    sqs_final_queue_url: str = os.getenv("SQS_FINAL_QUEUE_URL", "")

    # stable-whisper model
    model_name: str = os.getenv("WHISPER_MODEL", "large")
    device: str = os.getenv("DEVICE", "cuda")
    language: str = os.getenv("LANGUAGE", "he")
    token_step: int = int(os.getenv("TOKEN_STEP", "200"))

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")


config = Config()
