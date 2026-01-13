"""Tests for handlers layer."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from gpu_instance.models.schemas import SQSMessage, TranscriptionResult
from gpu_instance.services.s3_downloader import S3Downloader
from gpu_instance.services.s3_uploader import S3Uploader
from gpu_instance.handlers.transcription import process_message


class TestProcessMessage:
    """Tests for process_message handler."""

    @patch("gpu_instance.handlers.transcription.transcribe")
    @patch("gpu_instance.handlers.transcription.collect_segments")
    @patch("gpu_instance.handlers.transcription.segments_to_vtt")
    @patch("gpu_instance.handlers.transcription.segments_to_text")
    @patch("gpu_instance.handlers.transcription.segments_to_timed_text")
    @patch("gpu_instance.handlers.transcription.save_vtt")
    @patch("gpu_instance.handlers.transcription.save_text")
    @patch("gpu_instance.handlers.transcription.save_timed_text")
    def test_process_message_success(
        self,
        mock_save_timed,
        mock_save_text,
        mock_save_vtt,
        mock_to_timed,
        mock_to_text,
        mock_to_vtt,
        mock_collect,
        mock_transcribe,
        tmp_path,
    ):
        """Test process_message succeeds with all steps."""
        # Setup mocks
        mock_s3_downloader = MagicMock(spec=S3Downloader)
        mock_s3_uploader = MagicMock(spec=S3Uploader)

        audio_path = tmp_path / "test.mp3"
        audio_path.touch()
        mock_s3_downloader.download_audio.return_value = audio_path

        mock_transcribe.return_value = (iter([]), {"language": "he"})
        mock_collect.return_value = []
        mock_to_vtt.return_value = "WEBVTT"
        mock_to_text.return_value = "text"
        mock_to_timed.return_value = "timed"

        vtt_path = tmp_path / "test.vtt"
        text_path = tmp_path / "test.txt"
        timed_path = tmp_path / "test.timed.txt"
        mock_save_vtt.return_value = vtt_path
        mock_save_text.return_value = text_path
        mock_save_timed.return_value = timed_path

        mock_s3_uploader.upload_transcription.side_effect = [
            "output/test.vtt",
            "output/test.txt",
            "output/test.timed.txt",
        ]

        message = SQSMessage(
            s3_key="test.mp3",
            language="hebrew",
            massechet_name="Bava Kamma",
            daf_name="20",
        )

        result = process_message(
            message=message,
            s3_downloader=mock_s3_downloader,
            s3_uploader=mock_s3_uploader,
            temp_dir=tmp_path,
        )

        assert result.success is True
        assert result.source_key == "test.mp3"
        assert result.vtt_key == "output/test.vtt"
        assert result.text_key == "output/test.txt"
        assert result.timed_key == "output/test.timed.txt"

        mock_s3_downloader.download_audio.assert_called_once()
        assert mock_s3_uploader.upload_transcription.call_count == 3

    def test_process_message_download_failure(self, tmp_path):
        """Test process_message returns failure when download fails."""
        mock_s3_downloader = MagicMock(spec=S3Downloader)
        mock_s3_uploader = MagicMock(spec=S3Uploader)

        mock_s3_downloader.download_audio.return_value = None

        message = SQSMessage(
            s3_key="test.mp3",
            language="hebrew",
            massechet_name="Bava Kamma",
            daf_name="20",
        )

        result = process_message(
            message=message,
            s3_downloader=mock_s3_downloader,
            s3_uploader=mock_s3_uploader,
            temp_dir=tmp_path,
        )

        assert result.success is False
        assert result.source_key == "test.mp3"
        mock_s3_uploader.upload_transcription.assert_not_called()

    @patch("gpu_instance.handlers.transcription.transcribe")
    def test_process_message_transcription_failure(
        self,
        mock_transcribe,
        tmp_path,
    ):
        """Test process_message returns failure when transcription fails."""
        mock_s3_downloader = MagicMock(spec=S3Downloader)
        mock_s3_uploader = MagicMock(spec=S3Uploader)

        audio_path = tmp_path / "test.mp3"
        audio_path.touch()
        mock_s3_downloader.download_audio.return_value = audio_path

        mock_transcribe.side_effect = Exception("Transcription error")

        message = SQSMessage(
            s3_key="test.mp3",
            language="hebrew",
            massechet_name="Bava Kamma",
            daf_name="20",
        )

        result = process_message(
            message=message,
            s3_downloader=mock_s3_downloader,
            s3_uploader=mock_s3_uploader,
            temp_dir=tmp_path,
        )

        assert result.success is False
        assert result.source_key == "test.mp3"
