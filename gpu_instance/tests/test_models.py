"""Tests for Pydantic models."""

import pytest

from gpu_instance.models.schemas import SQSMessage, TranscriptionResult


class TestSQSMessage:
    """Tests for SQSMessage model."""

    def test_create_sqs_message(self):
        """Test creating an SQS message with all fields."""
        message = SQSMessage(
            s3_key="123456.mp3",
            language="hebrew",
            massechet_name="Bava Kamma",
            daf_name="20",
            receipt_handle="test-receipt-handle",
        )

        assert message.s3_key == "123456.mp3"
        assert message.language == "hebrew"
        assert message.massechet_name == "Bava Kamma"
        assert message.daf_name == "20"
        assert message.receipt_handle == "test-receipt-handle"

    def test_create_sqs_message_without_receipt_handle(self):
        """Test creating an SQS message without receipt handle."""
        message = SQSMessage(
            s3_key="123456.mp3",
            language="hebrew",
            massechet_name="Bava Kamma",
            daf_name="20",
        )

        assert message.s3_key == "123456.mp3"
        assert message.receipt_handle is None

    def test_sqs_message_from_dict(self):
        """Test creating SQS message from dict (like from JSON)."""
        data = {
            "s3_key": "test.mp3",
            "language": "english",
            "massechet_name": "Sanhedrin",
            "daf_name": "5",
        }
        message = SQSMessage(**data)

        assert message.s3_key == "test.mp3"
        assert message.language == "english"


class TestTranscriptionResult:
    """Tests for TranscriptionResult model."""

    def test_create_successful_result(self):
        """Test creating a successful transcription result."""
        result = TranscriptionResult(
            source_key="input.mp3",
            vtt_key="output.vtt",
            text_key="output.txt",
            timed_key="output.timed.txt",
            success=True,
        )

        assert result.source_key == "input.mp3"
        assert result.vtt_key == "output.vtt"
        assert result.text_key == "output.txt"
        assert result.timed_key == "output.timed.txt"
        assert result.success is True

    def test_create_failed_result(self):
        """Test creating a failed transcription result."""
        result = TranscriptionResult(
            source_key="input.mp3",
            success=False,
        )

        assert result.source_key == "input.mp3"
        assert result.vtt_key is None
        assert result.text_key is None
        assert result.timed_key is None
        assert result.success is False
