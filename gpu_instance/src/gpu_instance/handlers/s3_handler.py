"""S3 operations for downloading audio and uploading transcriptions."""

import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from gpu_instance.config import config

logger = logging.getLogger(__name__)


class S3Handler:
    """Handle S3 file operations."""

    def __init__(self, temp_dir: str):
        self.client = boto3.client("s3", region_name=config.aws_region)
        self.bucket = config.bucket_name
        self.temp_dir = temp_dir

    def download_audio(self, s3_key: str) -> str:
        """
        Download audio file from S3 to local temp directory.

        Args:
            s3_key: The S3 object key (e.g., 'audio-input/file.mp3')

        Returns:
            Local file path where the audio was downloaded.

        Raises:
            ClientError: If download fails.
        """
        filename = Path(s3_key).name
        local_path = os.path.join(self.temp_dir, filename)

        logger.info(f"Downloading s3://{self.bucket}/{s3_key} to {local_path}")

        try:
            self.client.download_file(self.bucket, s3_key, local_path)
            logger.info(f"Successfully downloaded {filename}")
            return local_path
        except ClientError as e:
            logger.error(f"Failed to download {s3_key}: {e}")
            raise

    def upload_transcription(self, local_path: str, original_s3_key: str) -> str:
        """
        Upload VTT transcription to S3.

        Args:
            local_path: Path to the local VTT file.
            original_s3_key: Original audio file S3 key (for deriving output key).

        Returns:
            The S3 key where the transcription was uploaded.

        Raises:
            ClientError: If upload fails.
        """
        filename = os.path.basename(original_s3_key)
        base_name = os.path.splitext(filename)[0]
        output_key = f"{config.output_prefix}{base_name}.vtt"

        logger.info(f"Uploading transcription to s3://{self.bucket}/{output_key}")

        try:
            self.client.upload_file(
                local_path,
                self.bucket,
                output_key,
                ExtraArgs={
                    "ContentType": "text/vtt",
                    "Metadata": {"source_audio": original_s3_key},
                },
            )
            logger.info(f"Successfully uploaded {output_key}")
            return output_key
        except ClientError as e:
            logger.error(f"Failed to upload transcription: {e}")
            raise

    def cleanup_local_file(self, local_path: str) -> None:
        """Remove local temporary file."""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.debug(f"Cleaned up {local_path}")
        except OSError as e:
            logger.warning(f"Failed to cleanup {local_path}: {e}")
