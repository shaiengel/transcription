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
        self.temp_dir = Path(temp_dir)

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
        local_path = self.temp_dir / filename        

        logger.info(f"Downloading s3://{self.bucket}/{s3_key} to {local_path}")

        try:
            self.client.download_file(self.bucket, s3_key, str(local_path))
            logger.info(f"Successfully downloaded {filename}")
            return str(local_path)
        except ClientError as e:
            logger.error(f"Failed to download {s3_key}: {e}")
            raise

    def upload_file(self, local_path: Path, original_s3_key: str) -> str:
        """
        Upload a file to S3.

        Args:
            local_path: Path to the local file.
            original_s3_key: Original audio file S3 key (for metadata).

        Returns:
            The S3 key where the file was uploaded.

        Raises:
            ClientError: If upload fails.
        """
        content_types = {
            ".vtt": "text/vtt",
            ".txt": "text/plain",
        }
        content_type = content_types.get(local_path.suffix, "application/octet-stream")
        output_key = f"{config.output_prefix}{local_path.name}"

        logger.info(f"Uploading to s3://{self.bucket}/{output_key}")

        try:
            self.client.upload_file(
                str(local_path),
                self.bucket,
                output_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"source_audio": original_s3_key},
                },
            )
            logger.info(f"Successfully uploaded {output_key}")
            return output_key
        except ClientError as e:
            logger.error(f"Failed to upload {local_path.name}: {e}")
            raise

    def cleanup_local_file(self, local_path: str) -> None:
        """Remove local temporary file."""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.debug(f"Cleaned up {local_path}")
        except OSError as e:
            logger.warning(f"Failed to cleanup {local_path}: {e}")
