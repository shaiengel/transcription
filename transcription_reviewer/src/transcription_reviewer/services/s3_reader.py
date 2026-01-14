"""S3 reader service for fetching timed transcriptions."""

import logging

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.schemas import TimedTranscription

logger = logging.getLogger(__name__)


class S3Reader:
    """Reads timed transcription files from S3."""

    TIMED_SUFFIX = ".timed.txt"

    def __init__(self, s3_client: S3Client):
        """
        Initialize S3 reader.

        Args:
            s3_client: S3Client instance for AWS operations.
        """
        self._s3_client = s3_client

    def list_timed_transcriptions(
        self,
        bucket: str,
        prefix: str = "",
    ) -> list[TimedTranscription]:
        """
        List all timed transcription files in S3.

        Args:
            bucket: S3 bucket name.
            prefix: Optional prefix to filter objects.

        Returns:
            List of TimedTranscription objects.
        """
        objects = self._s3_client.list_objects(
            bucket=bucket,
            prefix=prefix,
            suffix=self.TIMED_SUFFIX,
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
            "Found %d timed transcription files in s3://%s/%s",
            len(transcriptions),
            bucket,
            prefix,
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
