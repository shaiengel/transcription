"""Transcription handler for orchestrating the transcription pipeline."""

import logging
import tempfile
from pathlib import Path

from gpu_instance.models.schemas import SQSMessage, TranscriptionResult
from gpu_instance.models.formatter import Formatter
from gpu_instance.services.s3_downloader import S3Downloader
from gpu_instance.services.s3_uploader import S3Uploader
from gpu_instance.services.sqs_receiver import SQSReceiver
from gpu_instance.services.dynamo_reader import DynamoReader
from gpu_instance.services.transcriber import transcribe
from gpu_instance.services.segment_collector import collect_segments

logger = logging.getLogger(__name__)


def process_message(
    message: SQSMessage,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    dynamo_reader: DynamoReader,
    formatters: list[Formatter],
    temp_dir: Path,
) -> TranscriptionResult:
    """Process a single SQS message (transcribe audio file)."""
    media_id = message.media_id
    logger.info("Processing media_id=%s", media_id)

    entry = dynamo_reader.get_entry(media_id)
    if not entry:
        logger.error("No DynamoDB entry for media_id=%s, skipping", media_id)
        return TranscriptionResult(source_key=str(media_id), success=False)

    if not entry.media_bucket_s3:
        logger.error("media_bucket_s3 is not set for media_id=%s", media_id)
        return TranscriptionResult(source_key=str(media_id), success=False)

    s3_key = f"{media_id}.mp3"
    source_bucket = entry.media_bucket_s3
    dest_bucket = entry.media_transcribed_bucket

    logger.info(
        "Downloading s3://%s/%s -> transcribing -> uploading to s3://%s/",
        source_bucket, s3_key, dest_bucket,
    )

    try:
        audio_path = s3_downloader.download_audio(source_bucket, s3_key, temp_dir)
        if not audio_path:
            logger.error("Failed to download audio: %s", s3_key)
            return TranscriptionResult(source_key=s3_key, success=False)

        segments_iter, info = transcribe(str(audio_path))
        if segments_iter is None:
            return TranscriptionResult(source_key=s3_key, success=False)

        segments = collect_segments(segments_iter)
        if not segments:
            return TranscriptionResult(source_key=s3_key, success=False)

        audio_stem = audio_path.stem
        for formatter in formatters:
            content = formatter.format(segments)
            filename = f"{audio_stem}{formatter.extension}"
            s3_uploader.upload_content(content, filename, s3_key, dest_bucket)

        dynamo_reader.set_status(media_id, "transcribed")
        logger.info("Successfully processed media_id=%s", media_id)
        return TranscriptionResult(source_key=s3_key, success=True)

    except Exception as e:
        logger.error("Failed to process media_id=%s: %s", media_id, e, exc_info=True)
        return TranscriptionResult(source_key=s3_key, success=False)


def run_worker_loop(
    sqs_receiver: SQSReceiver,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    dynamo_reader: DynamoReader,
    formatters: list[Formatter],
) -> None:
    """Continuous worker loop that polls SQS and processes messages."""
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
            messages = sqs_receiver.receive_messages(max_messages=1, wait_time=20)

            if not messages:
                logger.debug("No messages received, continuing to poll...")
                continue

            for message in messages:
                result = process_message(
                    message=message,
                    s3_downloader=s3_downloader,
                    s3_uploader=s3_uploader,
                    dynamo_reader=dynamo_reader,
                    formatters=formatters,
                    temp_dir=temp_path,
                )

                if result.success:
                    success_count += 1
                else:
                    fail_count += 1

                sqs_receiver.delete_message(message)

                logger.info("Stats: %d success, %d failed", success_count, fail_count)
