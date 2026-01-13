"""S3 download service for audio files."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from gpu_instance.infrastructure.s3_client import S3Client

load_dotenv()
logger = logging.getLogger(__name__)


class S3Downloader:
    """Handles downloading audio files from S3."""

    def __init__(self, s3_client: S3Client):
        """
        Initialize S3 downloader.

        Args:
            s3_client: S3Client instance.
        """
        self._s3_client = s3_client
        self._source_bucket = os.getenv("SOURCE_BUCKET", "portal-daf-yomi-audio")

    @property
    def source_bucket(self) -> str:
        """Get the source bucket name."""
        return self._source_bucket

    def download_audio(self, s3_key: str, temp_dir: Path) -> Path | None:
        """
        Download audio file from S3 to local temp directory.

        Args:
            s3_key: S3 object key.
            temp_dir: Local temporary directory.

        Returns:
            Path to downloaded file, or None if download failed.
        """
        filename = Path(s3_key).name
        local_path = temp_dir / filename

        success = self._s3_client.download_file(
            bucket=self._source_bucket,
            key=s3_key,
            local_path=local_path,
        )

        if success:
            return local_path
        return None
