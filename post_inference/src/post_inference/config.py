"""Configuration management for the post-inference Lambda."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Post-inference configuration loaded from environment variables."""

    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    transcription_bucket: str = os.getenv(
        "TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription"
    )
    output_bucket: str = os.getenv("OUTPUT_BUCKET", "final-transcription")
    audio_bucket: str = os.getenv("AUDIO_BUCKET", "portal-daf-yomi-audio")
    sqs_queue_url: str = os.getenv("SQS_QUEUE_URL", "")


config = Config()
