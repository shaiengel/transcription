"""S3 reader service for fetching timed transcriptions."""

import logging

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.schemas import TimedTranscription

logger = logging.getLogger(__name__)


class S3Reader:
    """Reads timed transcription files from S3."""    

    def __init__(self, s3_client: S3Client):
        """
        Initialize S3 reader.

        Args:
            s3_client: S3Client instance for AWS operations.
        """
        self._s3_client = s3_client

    def list_transcriptions(
        self,
        bucket: str,
        prefix: str = "",
        suffix: str = "",
    ) -> list[TimedTranscription]:
        """
        List all transcription files in S3.

        Args:
            bucket: S3 bucket name.
            prefix: Optional prefix to filter objects.
            suffix: Optional suffix to filter objects.

        Returns:
            List of TimedTranscription objects.
        """
        objects = self._s3_client.list_objects(
            bucket=bucket,
            prefix=prefix,
            suffix=suffix,
        )

        transcriptions = []
        for obj in objects:
            key = obj["Key"]
            filename = key.split("/")[-1]
            transcriptions.append(
                TimedTranscription(
                    bucket=bucket,
                    key=key,
                    filename=filename,
                )
            )

        logger.info(
            f"Found {len(transcriptions)} transcription files in s3://{bucket}/{prefix}***{suffix}"            
        )
        return transcriptions

    def get_transcription_content(
        self,
        transcription: TimedTranscription,
    ) -> str | None:
        """
        Get the content of a timed transcription file.

        Args:
            transcription: TimedTranscription object.

        Returns:
            File content as string, or None if failed.
        """
        return self._s3_client.get_object_content(
            bucket=transcription.bucket,
            key=transcription.key,
        )

    def get_content_from_bucket(self, key: str, bucket: str) -> str | None:
        """
        Get content from a specific bucket and key.

        Args:
            key: S3 object key.
            bucket: S3 bucket name.

        Returns:
            File content as string, or None if failed.
        """
        return self._s3_client.get_object_content(bucket=bucket, key=key)
