"""S3 upload service for alignment output files."""

import logging
from pathlib import Path

from gpu_timestamp.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles uploading alignment output files to S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def upload_file(
        self,
        local_path: Path,
        s3_key: str,
        output_bucket: str,
        source_audio: str | None = None,
    ) -> bool:
        """
        Upload a file to S3.

        Args:
            local_path: Path to the local file.
            s3_key: S3 object key.
            source_audio: Original audio file key (for metadata).

        Returns:
            True if upload succeeded, False otherwise.
        """
        try:
            if not local_path.exists():
                logger.error("Local file does not exist: %s", local_path)
                return False

            content_types = {
                ".vtt": "text/vtt",
                ".json": "application/json",
                ".txt": "text/plain",
                ".srt": "text/plain",
                ".analysis": "application/json",
            }
            content_type = content_types.get(local_path.suffix, "application/octet-stream")

            metadata = {}
            if source_audio:
                metadata["source_audio"] = source_audio

            success = self._s3_client.upload_file(
                local_path=local_path,
                bucket=output_bucket,
                key=s3_key,
                content_type=content_type,
                metadata=metadata if metadata else None,
            )

            return success

        except Exception as e:
            logger.error("Error uploading %s: %s", local_path, e, exc_info=True)
            return False

    def upload_content(
        self,
        content: str,
        s3_key: str,
        output_bucket: str,
        source_audio: str | None = None,
    ) -> bool:
        try:
            content_types = {
                ".vtt": "text/vtt",
                ".json": "application/json",
                ".txt": "text/plain",
                ".srt": "text/plain",
                ".analysis": "application/json",
            }
            suffix = Path(s3_key).suffix
            content_type = content_types.get(suffix, "application/octet-stream")

            metadata = {}
            if source_audio:
                metadata["source_audio"] = source_audio

            success = self._s3_client.put_object(
                bucket=output_bucket,
                key=s3_key,
                body=content.encode("utf-8"),
                content_type=content_type,
                metadata=metadata if metadata else None,
            )

            return success

        except Exception as e:
            logger.error("Error uploading content to %s: %s", s3_key, e, exc_info=True)
            return False
