"""Transcription handler for orchestrating the transcription pipeline."""

import logging
import tempfile
from pathlib import Path

from gpu_instance.models.schemas import SQSMessage, TranscriptionResult
from gpu_instance.models.formatter import Formatter
from gpu_instance.services.s3_downloader import S3Downloader
from gpu_instance.services.s3_uploader import S3Uploader
from gpu_instance.services.sqs_receiver import SQSReceiver
from gpu_instance.services.transcriber import transcribe
from gpu_instance.services.segment_collector import collect_segments

logger = logging.getLogger(__name__)


def process_message(
    message: SQSMessage,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    formatters: list[Formatter],
    temp_dir: Path,
) -> TranscriptionResult:
    """
    Process a single SQS message (transcribe audio file).

    Args:
        message: SQS message containing file info.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        formatters: List of formatter instances.
        temp_dir: Temporary directory for file operations.

    Returns:
        TranscriptionResult with success status.
    """
    s3_key = message.s3_key
    logger.info(
        "Processing: %s (language=%s, details=%s)",
        s3_key,
        message.language,
        message.details,
    )

    try:
        # Download audio from S3
        audio_path = s3_downloader.download_audio(s3_key, temp_dir)
        if not audio_path:
            logger.error("Failed to download audio: %s", s3_key)
            return TranscriptionResult(source_key=s3_key, success=False)

        # Transcribe
        segments_iter, info = transcribe(str(audio_path))
        if segments_iter is None:
            return TranscriptionResult(source_key=s3_key, success=False)

        # Collect all segments
        segments = collect_segments(segments_iter)
        if not segments:
            return TranscriptionResult(source_key=s3_key, success=False)

        # Format and upload directly to S3 (no disk save)
        audio_stem = audio_path.stem
        for formatter in formatters:
            content = formatter.format(segments)
            filename = f"{audio_stem}{formatter.extension}"
            s3_uploader.upload_content(content, filename, s3_key)

        logger.info("Successfully processed %s", s3_key)

        return TranscriptionResult(source_key=s3_key, success=True)

    except Exception as e:
        logger.error("Failed to process %s: %s", s3_key, e, exc_info=True)
        return TranscriptionResult(source_key=s3_key, success=False)


def run_worker_loop(
    sqs_receiver: SQSReceiver,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    formatters: list[Formatter],
) -> None:
    """
    Continuous worker loop that polls SQS and processes messages.

    Args:
        sqs_receiver: SQS receiver service.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        formatters: List of formatter instances.
    """
    logger.info("Starting continuous worker loop...")

    success_count = 0
    fail_count = 0

    with tempfile.TemporaryDirectory(
        prefix="transcription_",
        ignore_cleanup_errors=True,
    ) as temp_dir:
        temp_path = Path(temp_dir)
        logger.info("Using temp directory: %s", temp_path)

        while True:
            # Poll for messages
            messages = sqs_receiver.receive_messages(max_messages=1, wait_time=20)

            if not messages:
                logger.debug("No messages received, continuing to poll...")
                continue

            for message in messages:
                result = process_message(
                    message=message,
                    s3_downloader=s3_downloader,
                    s3_uploader=s3_uploader,
                    formatters=formatters,
                    temp_dir=temp_path,
                )

                if result.success:
                    success_count += 1
                else:
                    fail_count += 1

                # Always delete message from queue
                sqs_receiver.delete_message(message)

                logger.info(
                    "Stats: %d success, %d failed", success_count, fail_count
                )
