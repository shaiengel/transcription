"""Transcription handler for orchestrating the transcription pipeline."""

import logging
import tempfile
from pathlib import Path

from gpu_instance.models.schemas import SQSMessage, TranscriptionResult
from gpu_instance.services.s3_downloader import S3Downloader
from gpu_instance.services.s3_uploader import S3Uploader
from gpu_instance.services.sqs_receiver import SQSReceiver
from gpu_instance.services.sqs_publisher import SQSPublisher
from gpu_instance.services import (
    transcribe,
    collect_segments,
    segments_to_vtt,
    segments_to_text,
    segments_to_timed_text,
    save_vtt,
    save_text,
    save_timed_text,
)

logger = logging.getLogger(__name__)


def process_message(
    message: SQSMessage,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_publisher: SQSPublisher,
    temp_dir: Path,
) -> TranscriptionResult:
    """
    Process a single SQS message (transcribe audio file).

    Args:
        message: SQS message containing file info.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        sqs_publisher: SQS publisher for completion messages.
        temp_dir: Temporary directory for file operations.

    Returns:
        TranscriptionResult with output keys and success status.
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

        # Convert to different formats
        vtt_content = segments_to_vtt(segments)
        text_content = segments_to_text(segments)
        timed_content = segments_to_timed_text(segments)

        # Save locally
        vtt_path = save_vtt(vtt_content, str(audio_path), str(temp_dir))
        text_path = save_text(text_content, str(audio_path), str(temp_dir))
        timed_path = save_timed_text(timed_content, str(audio_path), str(temp_dir))

        # Upload to S3
        vtt_key = s3_uploader.upload_transcription(vtt_path, s3_key)
        text_key = s3_uploader.upload_transcription(text_path, s3_key)
        timed_key = s3_uploader.upload_transcription(timed_path, s3_key)

        logger.info(
            "Successfully processed %s -> %s, %s, %s",
            s3_key,
            vtt_key,
            text_key,
            timed_key,
        )

        # Publish completion message to SQS
        sqs_publisher.publish_transcription_complete(
            timed_key=timed_key,
            details=message.details,
            language=message.language,
        )

        return TranscriptionResult(
            source_key=s3_key,
            vtt_key=vtt_key,
            text_key=text_key,
            timed_key=timed_key,
            success=True,
        )

    except Exception as e:
        logger.error("Failed to process %s: %s", s3_key, e, exc_info=True)
        return TranscriptionResult(source_key=s3_key, success=False)


def run_worker_loop(
    sqs_receiver: SQSReceiver,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_publisher: SQSPublisher,
) -> None:
    """
    Continuous worker loop that polls SQS and processes messages.

    Args:
        sqs_receiver: SQS receiver service.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        sqs_publisher: SQS publisher for completion messages.
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
                    sqs_publisher=sqs_publisher,
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
