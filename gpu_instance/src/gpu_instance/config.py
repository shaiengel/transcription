"""Configuration management for the transcription worker."""

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
    aws_role_arn: str = os.getenv(
        "AWS_ROLE_ARN", "arn:aws:iam::707072965202:role/gpu-transcription-role"
    )

    # S3 Buckets
    source_bucket: str = os.getenv("SOURCE_BUCKET", "portal-daf-yomi-audio")
    dest_bucket: str = os.getenv("DEST_BUCKET", "portal-daf-yomi-transcription")
    output_prefix: str = os.getenv("OUTPUT_PREFIX", "")

    # SQS
    sqs_queue_url: str = os.getenv("SQS_QUEUE_URL", "")

    # Whisper model
    model_name: str = os.getenv("WHISPER_MODEL", "ivrit-ai/whisper-large-v3-ct2")
    device: str = os.getenv("DEVICE", "cuda")
    compute_type: str = os.getenv("COMPUTE_TYPE", "int8_float16")
    language: str = os.getenv("LANGUAGE", "he")
    beam_size: int = int(os.getenv("BEAM_SIZE", "5"))

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")


config = Config()
