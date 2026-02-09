"""S3 client wrapper for AWS operations."""

import logging
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3Client:
    """Handles S3 operations."""

    def __init__(self, client: Any):
        self._client = client

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        suffix: str = "",
    ) -> list[dict]:
        """List objects in S3 bucket with optional prefix and suffix filter."""
        try:
            objects = []
            paginator = self._client.get_paginator("list_objects_v2")

            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith(suffix):
                        objects.append(obj)

            return objects
        except ClientError as e:
            logger.error("Failed to list objects in s3://%s/%s: %s", bucket, prefix, e)
            return []

    def get_object_content(self, bucket: str, key: str) -> str | None:
        """Get object content as string."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read().decode("utf-8")
        except ClientError as e:
            logger.error("Failed to get object s3://%s/%s: %s", bucket, key, e)
            return None

    def put_object_content(self, bucket: str, key: str, content: str) -> bool:
        """Upload string content to S3."""
        try:
            self._client.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
            logger.info("Uploaded content to s3://%s/%s", bucket, key)
            return True
        except ClientError as e:
            logger.error("Failed to upload to s3://%s/%s: %s", bucket, key, e)
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

    def delete_objects_by_prefix(self, bucket: str, prefix: str) -> int:
        """Delete all objects matching a prefix from S3."""
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
