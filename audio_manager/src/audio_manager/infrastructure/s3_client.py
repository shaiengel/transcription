import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class S3Client:
    """Handles S3 operations."""

    def __init__(self, client: Any):
        self._client = client

    def upload_file(self, file_path: Path, bucket: str, key: str) -> bool:
        """Upload a file to S3."""
        try:
            self._client.upload_file(str(file_path), bucket, key)
            logger.info("Uploaded: s3://%s/%s", bucket, key)
            return True
        except Exception as e:
            logger.error("Failed to upload %s: %s", file_path, e)
            return False
