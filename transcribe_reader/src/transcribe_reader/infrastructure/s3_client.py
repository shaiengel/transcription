"""S3 client wrapper for download operations."""

import logging
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3Client:
    """Handles S3 read operations."""

    def __init__(self, client: Any):
        self._client = client

    def file_exists(self, bucket: str, key: str) -> bool:
        """Check if a file exists in S3."""
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "404":
                return False
            if error_code == "403":
                logger.error(
                    "Access denied to s3://%s/%s - check IAM permissions for s3:HeadObject",
                    bucket,
                    key,
                )
            raise

    def download_content(self, bucket: str, key: str) -> str | None:
        """Download file content from S3 as string."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            logger.info("Downloaded: s3://%s/%s", bucket, key)
            return content
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning("File not found: s3://%s/%s", bucket, key)
                return None
            logger.error("Failed to download s3://%s/%s: %s", bucket, key, e)
            raise
