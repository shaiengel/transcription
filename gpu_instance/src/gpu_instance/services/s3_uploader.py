"""S3 upload service for transcription files."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from gpu_instance.infrastructure.s3_client import S3Client

load_dotenv()
logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles uploading transcription files to S3."""

    def __init__(self, s3_client: S3Client):
        """
        Initialize S3 uploader.

        Args:
            s3_client: S3Client instance.
        """
        self._s3_client = s3_client
        self._dest_bucket = os.getenv("DEST_BUCKET", "portal-daf-yomi-transcription")
        self._output_prefix = os.getenv("OUTPUT_PREFIX", "")

    @property
    def dest_bucket(self) -> str:
        """Get the destination bucket name."""
        return self._dest_bucket

    def upload_transcription(
        self,
        local_path: Path,
        original_key: str,
    ) -> str | None:
        """
        Upload a transcription file to S3.

        Args:
            local_path: Path to the local file.
            original_key: Original audio file S3 key (for metadata).

        Returns:
            S3 key where file was uploaded, or None if upload failed.
        """
        content_types = {
            ".vtt": "text/vtt",
            ".txt": "text/plain",
        }
        content_type = content_types.get(local_path.suffix, "application/octet-stream")
        output_key = f"{self._output_prefix}{local_path.name}"

        success = self._s3_client.upload_file(
            local_path=local_path,
            bucket=self._dest_bucket,
            key=output_key,
            content_type=content_type,
            metadata={"source_audio": original_key},
        )

        if success:
            return output_key
        return None
