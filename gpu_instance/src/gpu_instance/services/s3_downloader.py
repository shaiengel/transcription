"""S3 download service for audio files."""

import logging
from pathlib import Path

from gpu_instance.infrastructure.s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Downloader:
    """Handles downloading audio files from S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client

    def download_audio(self, bucket: str, s3_key: str, temp_dir: Path) -> Path | None:
        """
        Download audio file from S3 to local temp directory.

        Args:
            bucket: S3 bucket name.
            s3_key: S3 object key.
            temp_dir: Local temporary directory.

        Returns:
            Path to downloaded file, or None if download failed.
        """
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
