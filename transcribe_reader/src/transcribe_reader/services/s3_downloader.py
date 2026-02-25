"""S3 downloader service for transcription files."""

import logging
import os

from dotenv import load_dotenv

from transcribe_reader.infrastructure.s3_client import S3Client
from transcribe_reader.models.schemas import TranscriptionFile

logger = logging.getLogger(__name__)


class S3Downloader:
    """Downloads transcription files from S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client
        load_dotenv()
        self._default_bucket = os.getenv("S3_TRANSCRIPTION_BUCKET", "final-transcription")

    def _get_bucket(self, transcription_file: TranscriptionFile) -> str:
        """Resolve bucket for a given file."""
        return transcription_file.source_bucket or self._default_bucket

    def check_exists(self, transcription_file: TranscriptionFile) -> bool:
        """Check if file exists in S3."""
        bucket = self._get_bucket(transcription_file)
        exists = self._s3_client.file_exists(bucket, transcription_file.s3_key)
        transcription_file.exists_in_s3 = exists
        return exists

    def download(self, transcription_file: TranscriptionFile) -> bool:
        """Download file content from S3."""
        if not transcription_file.exists_in_s3:
            if not self.check_exists(transcription_file):
                return False

        bucket = self._get_bucket(transcription_file)
        content = self._s3_client.download_content(bucket, transcription_file.s3_key)
        if content:
            transcription_file.content = content
            return True
        return False
