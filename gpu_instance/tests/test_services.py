"""Tests for services layer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gpu_instance.infrastructure.s3_client import S3Client
from gpu_instance.infrastructure.sqs_client import SQSClient
from gpu_instance.models.schemas import SQSMessage
from gpu_instance.services.s3_downloader import S3Downloader
from gpu_instance.services.s3_uploader import S3Uploader
from gpu_instance.services.sqs_receiver import SQSReceiver


class TestS3Downloader:
    """Tests for S3Downloader."""

    @patch.dict("os.environ", {"SOURCE_BUCKET": "test-audio-bucket"})
    def test_download_audio_success(self, tmp_path):
        """Test download_audio returns path on success."""
        mock_s3_client = MagicMock(spec=S3Client)
        mock_s3_client.download_file.return_value = True

        downloader = S3Downloader(mock_s3_client)
        result = downloader.download_audio("test.mp3", tmp_path)

        assert result == tmp_path / "test.mp3"
        mock_s3_client.download_file.assert_called_once_with(
            bucket="test-audio-bucket",
            key="test.mp3",
            local_path=tmp_path / "test.mp3",
        )

    @patch.dict("os.environ", {"SOURCE_BUCKET": "test-audio-bucket"})
    def test_download_audio_failure(self, tmp_path):
        """Test download_audio returns None on failure."""
        mock_s3_client = MagicMock(spec=S3Client)
        mock_s3_client.download_file.return_value = False

        downloader = S3Downloader(mock_s3_client)
        result = downloader.download_audio("test.mp3", tmp_path)

        assert result is None

    @patch.dict("os.environ", {"SOURCE_BUCKET": "my-bucket"})
    def test_source_bucket_property(self):
        """Test source_bucket property returns configured bucket."""
        mock_s3_client = MagicMock(spec=S3Client)
        downloader = S3Downloader(mock_s3_client)

        assert downloader.source_bucket == "my-bucket"


class TestS3Uploader:
    """Tests for S3Uploader."""

    @patch.dict(
        "os.environ",
        {"DEST_BUCKET": "test-transcription-bucket", "OUTPUT_PREFIX": "output/"},
    )
    def test_upload_transcription_success(self, tmp_path):
        """Test upload_transcription returns key on success."""
        mock_s3_client = MagicMock(spec=S3Client)
        mock_s3_client.upload_file.return_value = True

        # Create test file
        test_file = tmp_path / "test.vtt"
        test_file.write_text("WEBVTT\n\ntest content")

        uploader = S3Uploader(mock_s3_client)
        result = uploader.upload_transcription(test_file, "original.mp3")

        assert result == "output/test.vtt"
        mock_s3_client.upload_file.assert_called_once()

    @patch.dict("os.environ", {"DEST_BUCKET": "test-bucket", "OUTPUT_PREFIX": ""})
    def test_upload_transcription_failure(self, tmp_path):
        """Test upload_transcription returns None on failure."""
        mock_s3_client = MagicMock(spec=S3Client)
        mock_s3_client.upload_file.return_value = False

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        uploader = S3Uploader(mock_s3_client)
        result = uploader.upload_transcription(test_file, "original.mp3")

        assert result is None

    @patch.dict("os.environ", {"DEST_BUCKET": "my-dest-bucket", "OUTPUT_PREFIX": ""})
    def test_dest_bucket_property(self):
        """Test dest_bucket property returns configured bucket."""
        mock_s3_client = MagicMock(spec=S3Client)
        uploader = S3Uploader(mock_s3_client)

        assert uploader.dest_bucket == "my-dest-bucket"


class TestSQSReceiver:
    """Tests for SQSReceiver."""

    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue"})
    def test_receive_messages_success(self):
        """Test receive_messages parses messages correctly."""
        mock_sqs_client = MagicMock(spec=SQSClient)
        mock_sqs_client.receive_messages.return_value = [
            {
                "Body": json.dumps(
                    {
                        "s3_key": "123.mp3",
                        "language": "hebrew",
                        "massechet_name": "Bava Kamma",
                        "daf_name": "20",
                    }
                ),
                "ReceiptHandle": "handle-123",
            }
        ]

        receiver = SQSReceiver(mock_sqs_client)
        messages = receiver.receive_messages()

        assert len(messages) == 1
        assert messages[0].s3_key == "123.mp3"
        assert messages[0].language == "hebrew"
        assert messages[0].massechet_name == "Bava Kamma"
        assert messages[0].daf_name == "20"
        assert messages[0].receipt_handle == "handle-123"

    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue"})
    def test_receive_messages_empty(self):
        """Test receive_messages returns empty list when no messages."""
        mock_sqs_client = MagicMock(spec=SQSClient)
        mock_sqs_client.receive_messages.return_value = []

        receiver = SQSReceiver(mock_sqs_client)
        messages = receiver.receive_messages()

        assert len(messages) == 0

    @patch.dict("os.environ", {"SQS_QUEUE_URL": ""})
    def test_receive_messages_no_queue_url(self):
        """Test receive_messages returns empty when queue URL not set."""
        mock_sqs_client = MagicMock(spec=SQSClient)

        receiver = SQSReceiver(mock_sqs_client)
        messages = receiver.receive_messages()

        assert len(messages) == 0
        mock_sqs_client.receive_messages.assert_not_called()

    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue"})
    def test_delete_message_success(self):
        """Test delete_message succeeds."""
        mock_sqs_client = MagicMock(spec=SQSClient)
        mock_sqs_client.delete_message.return_value = True

        receiver = SQSReceiver(mock_sqs_client)
        message = SQSMessage(
            s3_key="test.mp3",
            language="hebrew",
            massechet_name="Test",
            daf_name="1",
            receipt_handle="test-handle",
        )
        result = receiver.delete_message(message)

        assert result is True
        mock_sqs_client.delete_message.assert_called_once_with(
            queue_url="https://sqs.test/queue",
            receipt_handle="test-handle",
        )

    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue"})
    def test_delete_message_no_receipt_handle(self):
        """Test delete_message returns False when no receipt handle."""
        mock_sqs_client = MagicMock(spec=SQSClient)

        receiver = SQSReceiver(mock_sqs_client)
        message = SQSMessage(
            s3_key="test.mp3",
            language="hebrew",
            massechet_name="Test",
            daf_name="1",
        )
        result = receiver.delete_message(message)

        assert result is False
        mock_sqs_client.delete_message.assert_not_called()

    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue"})
    def test_queue_url_property(self):
        """Test queue_url property returns configured URL."""
        mock_sqs_client = MagicMock(spec=SQSClient)
        receiver = SQSReceiver(mock_sqs_client)

        assert receiver.queue_url == "https://sqs.test/queue"
