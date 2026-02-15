"""Configuration management for the transcription reviewer."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load .env if exists (local dev only, no-op in Lambda)
load_dotenv()


@lru_cache(maxsize=1)
def _get_secret(secret_name: str, key: str, region: str = "us-east-1") -> str:
    """Retrieve a secret from AWS Secrets Manager. Cached to avoid repeated API calls."""
    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_name)
        secret_data = json.loads(response["SecretString"])
        return secret_data.get(key, "")
    except ClientError as e:
        # Return empty string if secret not found (allows fallback to other config sources)
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return ""
        raise


def _load_json_config(filename: str) -> dict:
    """Load configuration from JSON file in .config directory."""
    config_path = Path(__file__).parent.parent.parent.parent / ".config" / filename
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


# Load config files
_config_dev = _load_json_config("config.dev.json")
_config_secrets = _load_json_config("config.secrets.dev.json")


def _get_config(key: str, default: str = "") -> str:
    """Get config value with priority: env var > json config > default."""
    # Check environment variable first (only if not empty)
    env_value = os.getenv(key.upper())
    if env_value:  # Treat empty string as missing
        return env_value

    # Check secrets file
    if key.lower() in _config_secrets:
        return _config_secrets[key.lower()]

    # Check dev config file
    if key.lower() in _config_dev:
        return str(_config_dev[key.lower()])

    return default


@dataclass
class Config:
    """Reviewer configuration loaded from env vars or JSON files."""

    # AWS
    aws_region: str = _get_config("AWS_REGION", "us-east-1")

    # S3 Buckets
    transcription_bucket: str = _get_config(
        "TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription"
    )
    transcription_prefix: str = _get_config("TRANSCRIPTION_PREFIX", "")
    template_bucket: str = _get_config("TEMPLATE_BUCKET", "portal-daf-yomi-audio")
    audio_bucket: str = _get_config("AUDIO_BUCKET", "portal-daf-yomi-audio")
    output_bucket: str = _get_config("OUTPUT_BUCKET", "portal-daf-yomi-fixed-text")

    # LLM Backend Selection
    llm_backend: str = _get_config("LLM_BACKEND", "AWS_OPUS4.5")

    # AWS Bedrock config
    batch_model_id: str = _get_config(
        "BATCH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0"
    )
    batch_role_arn: str = _get_config("BATCH_ROLE_ARN", "")
    min_entries: int = int(_get_config("MIN_ENTRIES", "100"))
    max_tokens: int = int(_get_config("MAX_TOKENS", "60000"))
    temperature: float = float(_get_config("TEMPERATURE", "0.4"))

    # Google Gemini config
    google_api_key: str = (
        _get_config("GOOGLE_API_KEY", "")  # Check env/local config first
        or _get_secret("google-api-key", "GOOGLE_API_KEY")  # Fall back to Secrets Manager
    )
    gemini_model: str = _get_config("GEMINI_MODEL", "gemini-2.0-flash")

    # SQS
    sqs_queue_url: str = _get_config("SQS_QUEUE_URL", "")

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.transcription_bucket:
            raise ValueError("TRANSCRIPTION_BUCKET environment variable is required")

        if self.llm_backend == "AWS_OPUS4.5" and not self.batch_role_arn:
            raise ValueError(
                "BATCH_ROLE_ARN environment variable is required for AWS_OPUS4.5 backend"
            )

        if self.llm_backend == "GEMINI2.5" and not self.google_api_key:
            raise ValueError(
                "GOOGLE_API_KEY environment variable is required for GEMINI2.5 backend"
            )


config = Config()
