"""S3 downloader service for VTT files."""

import logging
import os

from dotenv import load_dotenv

from transcribe_reader.infrastructure.s3_client import S3Client
from transcribe_reader.models.schemas import VttFile

logger = logging.getLogger(__name__)


class S3Downloader:
    """Downloads VTT files from S3."""

    def __init__(self, s3_client: S3Client):
        self._s3_client = s3_client
        load_dotenv()
        self._bucket = os.getenv("S3_TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")

    def check_exists(self, vtt_file: VttFile) -> bool:
        """Check if VTT file exists in S3."""
        exists = self._s3_client.file_exists(self._bucket, vtt_file.s3_key)
        vtt_file.exists_in_s3 = exists
        return exists

    def download(self, vtt_file: VttFile) -> bool:
        """Download VTT file content from S3."""
        if not vtt_file.exists_in_s3:
            if not self.check_exists(vtt_file):
                return False

        content = self._s3_client.download_content(self._bucket, vtt_file.s3_key)
        if content:
            vtt_file.content = content
            return True
        return False
