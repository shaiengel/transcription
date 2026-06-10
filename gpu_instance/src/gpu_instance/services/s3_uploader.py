"""S3 upload service for transcription files."""

import logging
from pathlib import Path

from gpu_instance.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles uploading transcription files to S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def upload_transcription(
        self,
        local_path: Path,
        original_key: str,
        bucket: str,
        overwrite: bool = True,
    ) -> str | None:
        """
        Upload a transcription file to S3.

        Args:
            local_path: Path to the local file.
            original_key: Original audio file S3 key (for metadata).
            bucket: Destination S3 bucket name.
            overwrite: If True, overwrite existing files. If False, skip if exists.

        Returns:
            S3 key where file was uploaded, or None if upload failed.
        """
        try:
            if not local_path.exists():
                logger.error("Local file does not exist: %s", local_path)
                return None

            content_types = {
                ".vtt": "text/vtt",
                ".txt": "text/plain",
            }
            content_type = content_types.get(local_path.suffix, "application/octet-stream")
            output_key = local_path.name

            if not overwrite and self._s3_client.file_exists(bucket, output_key):
                logger.info("File already exists, skipping upload: s3://%s/%s", bucket, output_key)
                return output_key

            success = self._s3_client.upload_file(
                local_path=local_path,
                bucket=bucket,
                key=output_key,
                content_type=content_type,
                metadata={"source_audio": original_key},
            )

            if success:
                return output_key

            logger.error("Upload failed for: %s", local_path)
            return None

        except Exception as e:
            logger.error("Error uploading transcription %s: %s", local_path, e, exc_info=True)
            return None

    def upload_content(
        self,
        content: str,
        filename: str,
        original_key: str,
        bucket: str,
    ) -> str | None:
        """
        Upload string content directly to S3.

        Args:
            content: Text content to upload.
            filename: Output filename (with extension).
            original_key: Original audio file S3 key (for metadata).
            bucket: Destination S3 bucket name.

        Returns:
            S3 key where content was uploaded, or None if upload failed.
        """
        try:
            ext = Path(filename).suffix
            content_types = {
                ".vtt": "text/vtt",
                ".txt": "text/plain",
            }
            content_type = content_types.get(ext, "text/plain")

            success = self._s3_client.put_object(
                bucket=bucket,
                key=filename,
                body=content.encode("utf-8"),
                content_type=content_type,
                metadata={"source_audio": original_key},
            )

            if success:
                return filename

            logger.error("Upload failed for content: %s", filename)
            return None

        except Exception as e:
            logger.error("Error uploading content %s: %s", filename, e, exc_info=True)
            return None
