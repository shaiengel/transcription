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

    # S3
    bucket_name: str = os.getenv("BUCKET_NAME", "")
    input_prefix: str = os.getenv("INPUT_PREFIX", "")
    output_prefix: str = os.getenv("OUTPUT_PREFIX", "transcriptions/")

    # Whisper model
    model_name: str = os.getenv("WHISPER_MODEL", "ivrit-ai/whisper-large-v3-ct2")
    device: str = os.getenv("DEVICE", "cuda")
    compute_type: str = os.getenv("COMPUTE_TYPE", "int8_float16")
    language: str = os.getenv("LANGUAGE", "he")
    beam_size: int = int(os.getenv("BEAM_SIZE", "5"))

    # Local paths
    temp_dir: str = os.getenv("TEMP_DIR", "/tmp/transcription")

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.bucket_name:
            raise ValueError("BUCKET_NAME environment variable is required")


config = Config()
