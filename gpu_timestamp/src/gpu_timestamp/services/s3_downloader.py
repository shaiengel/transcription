"""S3 download service for audio and text files."""

import logging
from pathlib import Path

from gpu_timestamp.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Downloader:
    """Handles downloading audio and text files from S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def download_audio(self, s3_key: str, temp_dir: Path, bucket: str) -> Path | None:
        filename = Path(s3_key).name
        local_path = temp_dir / filename

        success = self._s3_client.download_file(
            bucket=bucket,
            key=s3_key,
            local_path=local_path,
        )

        if success:
            return local_path
        return None

    def download_text(self, filename: str, bucket: str) -> str | None:
        content = self._s3_client.get_object_content(
            bucket=bucket,
            key=filename,
        )

        if content:
            logger.info("Downloaded text for %s: %d characters", filename, len(content))
            return content

        logger.error("Failed to download text: s3://%s/%s", bucket, filename)
        return None
