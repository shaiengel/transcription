"""S3 client wrapper for AWS operations."""

import logging
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3Client:
    """Handles S3 operations."""

    def __init__(self, client: Any):
        """
        Initialize S3 client wrapper.

        Args:
            client: boto3 S3 client instance.
        """
        self._client = client

    def file_exists(self, bucket: str, key: str) -> bool:
        """
        Check if a file exists in S3.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.

        Returns:
            True if file exists, False otherwise.
        """
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def download_file(self, bucket: str, key: str, local_path: Path) -> bool:
        """
        Download a file from S3.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.
            local_path: Local path to save the file.

        Returns:
            True if download succeeded, False otherwise.
        """
        try:
            logger.info("Downloading s3://%s/%s to %s", bucket, key, local_path)
            self._client.download_file(bucket, key, str(local_path))
            logger.info("Downloaded: %s", local_path.name)
            return True
        except ClientError as e:
            logger.error("Failed to download s3://%s/%s: %s", bucket, key, e)
            return False

    def upload_file(
        self,
        local_path: Path,
        bucket: str,
        key: str,
        content_type: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """
        Upload a file to S3.

        Args:
            local_path: Local file path.
            bucket: S3 bucket name.
            key: S3 object key.
            content_type: Optional content type.
            metadata: Optional metadata dict.

        Returns:
            True if upload succeeded, False otherwise.
        """
        try:
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            if metadata:
                extra_args["Metadata"] = metadata

            logger.info("Uploading %s to s3://%s/%s", local_path.name, bucket, key)
            self._client.upload_file(
                str(local_path),
                bucket,
                key,
                ExtraArgs=extra_args if extra_args else None,
            )
            logger.info("Uploaded: s3://%s/%s", bucket, key)
            return True
        except Exception as e:
            logger.error("Failed to upload %s: %s", local_path, e)
            return False
