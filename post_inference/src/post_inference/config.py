"""Configuration management for the post-inference Lambda."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path, override=True)


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

    # Gemini webhook (used when GeminiPostProcessing is active)
    google_webhook_audience: str = os.getenv("GOOGLE_WEBHOOK_AUDIENCE", "")
    google_jwks_url: str = os.getenv(
        "GOOGLE_JWKS_URL",
        "https://generativelanguage.googleapis.com/.well-known/jwks.json",
    )
    gemini_webhook_sign_secret: str = os.getenv("GEMINI_WEBHOOK_SIGN_SECRET", "")
    batch_jobs_table: str = os.getenv("BATCH_JOBS_TABLE", "transcription-batch-jobs")
    reviewer_function_name: str = os.getenv("REVIEWER_FUNCTION_NAME", "transcription-reviewer")


config = Config()
