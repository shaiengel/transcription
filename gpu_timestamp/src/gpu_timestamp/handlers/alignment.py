"""Alignment handler for orchestrating the audio-text alignment pipeline."""

import logging
import tempfile
from pathlib import Path

from gpu_timestamp.models.schemas import AlignmentResult, SQSMessage
from gpu_timestamp.services.aligner import align_audio, save_outputs
from gpu_timestamp.services.s3_downloader import S3Downloader
from gpu_timestamp.services.s3_uploader import S3Uploader
from gpu_timestamp.services.sqs_receiver import SQSReceiver
from gpu_timestamp.services.sqs_sender import SQSSender

logger = logging.getLogger(__name__)


def process_message(
    message: SQSMessage,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_sender: SQSSender,
    temp_dir: Path,
) -> AlignmentResult:
    """
    Process a single SQS message (align audio with text).

    Args:
        message: SQS message containing file info.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        sqs_sender: SQS sender service for completion notifications.
        temp_dir: Temporary directory for file operations.

    Returns:
        AlignmentResult with success status.
    """
    s3_key = message.s3_key
    stem = Path(s3_key).stem
    logger.info(
        "Processing: %s (language=%s)",
        s3_key,
        message.language,
    )

    try:
        # Download audio from S3
        audio_path = s3_downloader.download_audio(stem + ".mp3", temp_dir)
        if not audio_path:
            logger.error("Failed to download audio: %s", s3_key)
            return AlignmentResult(
                source_key=s3_key,
                success=False,
                error="Failed to download audio",
            )

        # Download text from S3
        text_content = s3_downloader.download_text(s3_key + ".txt")
        if not text_content:
            logger.error("Failed to download text for: %s", stem)
            return AlignmentResult(
                source_key=s3_key,
                success=False,
                error="Failed to download text",
            )

        # Align audio with text
        result = align_audio(str(audio_path), text_content, message.language)
        if result is None:
            logger.error("Alignment failed for: %s", s3_key)
            return AlignmentResult(
                source_key=s3_key,
                success=False,
                error="Alignment returned None",
            )

        # Save outputs locally
        json_path, vtt_path = save_outputs(result, temp_dir, stem)

        # Upload JSON and VTT to S3 (overwrites existing VTT)
        json_uploaded = s3_uploader.upload_file(
            json_path, f"{stem}.json", source_audio=s3_key
        )
        vtt_uploaded = s3_uploader.upload_file(
            vtt_path, f"{stem}.vtt", source_audio=s3_key
        )

        if not json_uploaded or not vtt_uploaded:
            logger.error("Failed to upload outputs for: %s", s3_key)
            return AlignmentResult(
                source_key=s3_key,
                success=False,
                error="Failed to upload outputs",
            )

        # Send completion notification to final queue
        sqs_sender.send_completion_message(
            stem=stem,
            source_audio=s3_key,
            vtt_key=f"{stem}.vtt",
            json_key=f"{stem}.json",
        )

        logger.info("Successfully processed %s", s3_key)
        return AlignmentResult(
            source_key=s3_key,
            success=True,
            output_key=f"{stem}.vtt",
        )

    except Exception as e:
        logger.error("Failed to process %s: %s", s3_key, e, exc_info=True)
        return AlignmentResult(
            source_key=s3_key,
            success=False,
            error=str(e),
        )


def run_worker_loop(
    sqs_receiver: SQSReceiver,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_sender: SQSSender,
) -> None:
    """
    Continuous worker loop that polls SQS and processes messages.

    Args:
        sqs_receiver: SQS receiver service.
        s3_downloader: S3 downloader service.
        s3_uploader: S3 uploader service.
        sqs_sender: SQS sender service for completion notifications.
    """
    logger.info("Starting continuous worker loop...")

    success_count = 0
    fail_count = 0

    with tempfile.TemporaryDirectory(
        prefix="timestamp_",
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
                    sqs_sender=sqs_sender,
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
