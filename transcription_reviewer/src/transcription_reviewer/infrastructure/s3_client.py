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

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        suffix: str = "",
    ) -> list[dict]:
        """
        List objects in S3 bucket with optional prefix and suffix filter.

        Args:
            bucket: S3 bucket name.
            prefix: Optional prefix to filter objects.
            suffix: Optional suffix to filter objects (e.g., '.timed.txt').

        Returns:
            List of object metadata dictionaries.
        """
        try:
            objects = []
            paginator = self._client.get_paginator("list_objects_v2")

            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith(suffix):
                        objects.append(obj)

            logger.info(
                "Found %d objects in s3://%s/%s with suffix '%s'",
                len(objects),
                bucket,
                prefix,
                suffix,
            )
            return objects
        except ClientError as e:
            logger.error("Failed to list objects in s3://%s/%s: %s", bucket, prefix, e)
            return []

    def get_object_content(self, bucket: str, key: str) -> str | None:
        """
        Get object content as string.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.

        Returns:
            Object content as string, or None if failed.
        """
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            logger.info("Read content from s3://%s/%s", bucket, key)
            return content
        except ClientError as e:
            logger.error("Failed to get object s3://%s/%s: %s", bucket, key, e)
            return None

    def get_object_bytes(self, bucket: str, key: str) -> bytes | None:
        """
        Get object content as bytes.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.

        Returns:
            Object content as bytes, or None if failed.
        """
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()
            logger.info("Read %d bytes from s3://%s/%s", len(content), bucket, key)
            return content
        except ClientError as e:
            logger.error("Failed to get object s3://%s/%s: %s", bucket, key, e)
            return None

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

    def put_object_content(self, bucket: str, key: str, content: str) -> bool:
        """
        Upload string content to S3.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.
            content: String content to upload.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
            logger.info("Uploaded content to s3://%s/%s", bucket, key)
            return True
        except ClientError as e:
            logger.error("Failed to upload to s3://%s/%s: %s", bucket, key, e)
            return False

    def download_file(self, bucket: str, key: str, local_path: str) -> bool:
        """
        Download a file from S3 to a local path.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.
            local_path: Local file path to save to.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client.download_file(bucket, key, local_path)
            logger.info("Downloaded s3://%s/%s to %s", bucket, key, local_path)
            return True
        except ClientError as e:
            logger.error("Failed to download s3://%s/%s: %s", bucket, key, e)
            return False

    def upload_file(self, file_path: Path, bucket: str, key: str) -> bool:
        """
        Upload a local file to S3.

        Args:
            file_path: Path to local file.
            bucket: S3 bucket name.
            key: S3 object key.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client.upload_file(str(file_path), bucket, key)
            logger.info("Uploaded file to s3://%s/%s", bucket, key)
            return True
        except ClientError as e:
            logger.error("Failed to upload file to s3://%s/%s: %s", bucket, key, e)
            return False
        
    def copy_object(self, source_bucket: str, source_key: str, dest_bucket: str, dest_key: str) -> bool:
        """Copy an object from one S3 location to another."""
        try:
            self._client.copy_object(
                Bucket=dest_bucket,
                Key=dest_key,
                CopySource={"Bucket": source_bucket, "Key": source_key},
            )
            logger.info("Copied s3://%s/%s -> s3://%s/%s", source_bucket, source_key, dest_bucket, dest_key)
            return True
        except ClientError as e:
            logger.error("Failed to copy s3://%s/%s: %s", source_bucket, source_key, e)
            return False        

    def delete_object(self, bucket: str, key: str) -> bool:
        """
        Delete a single object from S3.

        Args:
            bucket: S3 bucket name.
            key: S3 object key.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
            logger.info("Deleted s3://%s/%s", bucket, key)
            return True
        except ClientError as e:
            logger.error("Failed to delete s3://%s/%s: %s", bucket, key, e)
            return False

    def delete_objects_by_prefix(self, bucket: str, prefix: str) -> int:
        """
        Delete all objects matching a prefix from S3.

        Args:
            bucket: S3 bucket name.
            prefix: Prefix to match (e.g., 'filename.' to delete filename.*).

        Returns:
            Number of objects deleted.
        """
        try:
            objects = self.list_objects(bucket=bucket, prefix=prefix)
            if not objects:
                return 0

            delete_keys = [{"Key": obj["Key"]} for obj in objects]
            response = self._client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": delete_keys},
            )
            deleted_count = len(response.get("Deleted", []))
            logger.info(
                "Deleted %d objects from s3://%s/%s*",
                deleted_count,
                bucket,
                prefix,
            )
            return deleted_count
        except ClientError as e:
            logger.error("Failed to delete objects from s3://%s/%s*: %s", bucket, prefix, e)
            return 0
