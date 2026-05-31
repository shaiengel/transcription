import logging
from pathlib import Path

from audio_manager.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles S3 upload operations."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def upload_file(self, file_path: Path, bucket: str, key: str) -> bool:
        """Upload a file to S3."""
        return self._s3_client.upload_file(file_path, bucket, key)

    def upload_content(self, content: str, bucket: str, key: str) -> bool:
        """Upload string content to S3."""
        return self._s3_client.upload_content(content, bucket, key)
