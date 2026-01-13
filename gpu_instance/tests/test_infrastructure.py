"""Tests for infrastructure layer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from gpu_instance.infrastructure.s3_client import S3Client
from gpu_instance.infrastructure.sqs_client import SQSClient


class TestS3Client:
    """Tests for S3Client."""

    def test_file_exists_returns_true(self):
        """Test file_exists returns True when file exists."""
        mock_boto_client = MagicMock()
        mock_boto_client.head_object.return_value = {}

        client = S3Client(mock_boto_client)
        result = client.file_exists("test-bucket", "test-key")

        assert result is True
        mock_boto_client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="test-key"
        )

    def test_file_exists_returns_false_on_404(self):
        """Test file_exists returns False when file doesn't exist."""
        mock_boto_client = MagicMock()
        error_response = {"Error": {"Code": "404"}}
        mock_boto_client.head_object.side_effect = ClientError(
            error_response, "HeadObject"
        )

        client = S3Client(mock_boto_client)
        result = client.file_exists("test-bucket", "test-key")

        assert result is False

    def test_file_exists_raises_on_other_error(self):
        """Test file_exists raises on non-404 errors."""
        mock_boto_client = MagicMock()
        error_response = {"Error": {"Code": "403"}}
        mock_boto_client.head_object.side_effect = ClientError(
            error_response, "HeadObject"
        )

        client = S3Client(mock_boto_client)

        with pytest.raises(ClientError):
            client.file_exists("test-bucket", "test-key")

    def test_download_file_success(self, tmp_path):
        """Test download_file succeeds."""
        mock_boto_client = MagicMock()

        client = S3Client(mock_boto_client)
        local_path = tmp_path / "test.mp3"
        result = client.download_file("test-bucket", "test-key", local_path)

        assert result is True
        mock_boto_client.download_file.assert_called_once_with(
            "test-bucket", "test-key", str(local_path)
        )

    def test_download_file_failure(self, tmp_path):
        """Test download_file returns False on failure."""
        mock_boto_client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_boto_client.download_file.side_effect = ClientError(
            error_response, "GetObject"
        )

        client = S3Client(mock_boto_client)
        local_path = tmp_path / "test.mp3"
        result = client.download_file("test-bucket", "test-key", local_path)

        assert result is False

    def test_upload_file_success(self, tmp_path):
        """Test upload_file succeeds."""
        mock_boto_client = MagicMock()

        # Create a temp file
        local_path = tmp_path / "test.txt"
        local_path.write_text("test content")

        client = S3Client(mock_boto_client)
        result = client.upload_file(
            local_path=local_path,
            bucket="test-bucket",
            key="test-key",
            content_type="text/plain",
            metadata={"source": "test"},
        )

        assert result is True
        mock_boto_client.upload_file.assert_called_once()

    def test_upload_file_failure(self, tmp_path):
        """Test upload_file returns False on failure."""
        mock_boto_client = MagicMock()
        mock_boto_client.upload_file.side_effect = Exception("Upload failed")

        local_path = tmp_path / "test.txt"
        local_path.write_text("test content")

        client = S3Client(mock_boto_client)
        result = client.upload_file(
            local_path=local_path,
            bucket="test-bucket",
            key="test-key",
        )

        assert result is False


class TestSQSClient:
    """Tests for SQSClient."""

    def test_receive_messages_success(self):
        """Test receive_messages returns messages."""
        mock_boto_client = MagicMock()
        mock_boto_client.receive_message.return_value = {
            "Messages": [
                {"Body": '{"s3_key": "test.mp3"}', "ReceiptHandle": "handle-1"}
            ]
        }

        client = SQSClient(mock_boto_client)
        messages = client.receive_messages(
            queue_url="https://sqs.test/queue",
            max_messages=1,
            wait_time=20,
        )

        assert len(messages) == 1
        assert messages[0]["Body"] == '{"s3_key": "test.mp3"}'
        mock_boto_client.receive_message.assert_called_once()

    def test_receive_messages_empty(self):
        """Test receive_messages returns empty list when no messages."""
        mock_boto_client = MagicMock()
        mock_boto_client.receive_message.return_value = {}

        client = SQSClient(mock_boto_client)
        messages = client.receive_messages(
            queue_url="https://sqs.test/queue",
            max_messages=1,
            wait_time=20,
        )

        assert len(messages) == 0

    def test_receive_messages_error(self):
        """Test receive_messages returns empty list on error."""
        mock_boto_client = MagicMock()
        mock_boto_client.receive_message.side_effect = Exception("SQS error")

        client = SQSClient(mock_boto_client)
        messages = client.receive_messages(
            queue_url="https://sqs.test/queue",
            max_messages=1,
            wait_time=20,
        )

        assert len(messages) == 0

    def test_delete_message_success(self):
        """Test delete_message succeeds."""
        mock_boto_client = MagicMock()

        client = SQSClient(mock_boto_client)
        result = client.delete_message(
            queue_url="https://sqs.test/queue",
            receipt_handle="test-handle",
        )

        assert result is True
        mock_boto_client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.test/queue",
            ReceiptHandle="test-handle",
        )

    def test_delete_message_failure(self):
        """Test delete_message returns False on failure."""
        mock_boto_client = MagicMock()
        mock_boto_client.delete_message.side_effect = Exception("Delete failed")

        client = SQSClient(mock_boto_client)
        result = client.delete_message(
            queue_url="https://sqs.test/queue",
            receipt_handle="test-handle",
        )

        assert result is False
