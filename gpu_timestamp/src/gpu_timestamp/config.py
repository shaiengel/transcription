"""Configuration management for the timestamp alignment worker."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path, override=True)


@dataclass
class Config:
    """Worker configuration loaded from environment variables."""

    # AWS
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")

    local_dev: bool = os.getenv("LOCAL_DEV", "false").lower() in ("true", "1", "yes")

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
    whisper_cache: str | None = os.getenv("WHISPER_CACHE", None)
    rolling_avg_target: float = float(os.getenv("ROLLING_AVG_TARGET", "0.25"))

    # DTW alignment parameters
    dtw_enabled: bool = os.getenv("DTW_ENABLED", "true").lower() in ("true", "1", "yes")
    dtw_band_width: int = int(os.getenv("DTW_BAND_WIDTH", "0"))
    dtw_window_type: str = os.getenv("DTW_WINDOW_TYPE", "slantedband")
    dtw_step_pattern: str = os.getenv("DTW_STEP_PATTERN", "asymmetric")
    dtw_match_threshold: float = float(os.getenv("DTW_MATCH_THRESHOLD", "0.5"))
    dtw_high_dist_threshold: float = float(os.getenv("DTW_HIGH_DIST_THRESHOLD", "0.7"))
    dtw_low_score_threshold: float = float(os.getenv("DTW_LOW_SCORE_THRESHOLD", "0.5"))
    dtw_jump_threshold: int = int(os.getenv("DTW_JUMP_THRESHOLD", "40"))
    dtw_drop_threshold: float = float(os.getenv("DTW_DROP_THRESHOLD", "0.25"))
    dtw_ma_window: int = int(os.getenv("DTW_MA_WINDOW", "10"))

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")


config = Config()
