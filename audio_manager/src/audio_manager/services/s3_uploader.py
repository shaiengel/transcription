import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from audio_manager.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles S3 upload operations."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client
        load_dotenv()
        self._bucket = os.getenv("S3_BUCKET")

    def upload_file(self, file_path: Path, key: str) -> bool:
        """Upload a file to S3."""
        if not self._bucket:
            logger.error("S3_BUCKET not set in environment")
            return False
        return self._s3_client.upload_file(file_path, self._bucket, key)

    def upload_content(self, content: str, key: str) -> bool:
        """Upload string content to S3."""
        if not self._bucket:
            logger.error("S3_BUCKET not set in environment")
            return False
        return self._s3_client.upload_content(content, self._bucket, key)
