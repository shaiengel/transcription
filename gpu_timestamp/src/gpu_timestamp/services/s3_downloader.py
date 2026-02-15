"""S3 download service for audio and text files."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from gpu_timestamp.infrastructure.s3_client import S3Client

load_dotenv()
logger = logging.getLogger(__name__)


class S3Downloader:
    """Handles downloading audio and text files from S3."""

    def __init__(self, s3_client: S3Client):
        """
        Initialize S3 downloader.

        Args:
            s3_client: S3Client instance.
        """
        self._s3_client = s3_client
        self._audio_bucket = os.getenv("AUDIO_BUCKET", "portal-daf-yomi-audio")
        self._text_bucket = os.getenv("TEXT_BUCKET", "portal-daf-yomi-fixed-text")

    @property
    def audio_bucket(self) -> str:
        """Get the audio bucket name."""
        return self._audio_bucket

    @property
    def text_bucket(self) -> str:
        """Get the text bucket name."""
        return self._text_bucket

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
            bucket=self._audio_bucket,
            key=s3_key,
            local_path=local_path,
        )

        if success:
            return local_path
        return None

    def download_text(self, filename: str) -> str | None:
        """
        Download text file content from S3.

        Args:
            filename: Base filename (without extension).

        Returns:
            Text content as string, or None if download failed.
        """        

        content = self._s3_client.get_object_content(
            bucket=self._text_bucket,
            key=filename,
        )

        if content:
            logger.info("Downloaded text for %s: %d characters", filename, len(content))
            return content

        logger.error("Failed to download text: s3://%s/%s", self._text_bucket, filename)
        return None
