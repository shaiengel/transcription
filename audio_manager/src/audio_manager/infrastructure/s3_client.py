import logging
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3Client:
    """Handles S3 operations."""

    def __init__(self, client: Any):
        self._client = client

    def file_exists(self, bucket: str, key: str) -> bool:
        """Check if a file exists in S3."""
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def upload_file(self, file_path: Path, bucket: str, key: str) -> bool:
        """Upload a file to S3. Skips if file already exists."""
        try:
            if self.file_exists(bucket, key):
                logger.info("Already exists: s3://%s/%s", bucket, key)
                return True

            self._client.upload_file(str(file_path), bucket, key)
            logger.info("Uploaded: s3://%s/%s", bucket, key)
            return True
        except Exception as e:
            logger.error("Failed to upload %s: %s", file_path, e)
            return False

    def upload_content(self, content: str, bucket: str, key: str) -> bool:
        """Upload string content to S3. Skips if file already exists."""
        try:
            # if self.file_exists(bucket, key):
            #     logger.info("Already exists: s3://%s/%s", bucket, key)
            #     return True

            self._client.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
            logger.info("Uploaded: s3://%s/%s", bucket, key)
            return True
        except Exception as e:
            logger.error("Failed to upload content to %s: %s", key, e)
            return False
