"""Alignment handler for orchestrating the audio-text alignment pipeline."""

import json
import logging
import tempfile
from pathlib import Path

from gpu_timestamp.config import config
from gpu_timestamp.models.schemas import AlignmentResult, SQSMessage
from gpu_timestamp.services.aligner import align_audio, save_outputs
from gpu_timestamp.services.alignment_evaluator import (
    AlignmentEvaluator,
    truncate_srt_file,
    truncate_vtt_file,
)
from gpu_timestamp.services.dynamo_reader import DynamoReader
from gpu_timestamp.services.s3_downloader import S3Downloader
from gpu_timestamp.services.s3_uploader import S3Uploader
from gpu_timestamp.services.sqs_receiver import SQSReceiver
from gpu_timestamp.services.sqs_sender import SQSSender

logger = logging.getLogger(__name__)


def process_message(
    message: SQSMessage,
    dynamo_reader: DynamoReader,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_sender: SQSSender,
    temp_dir: Path,
) -> AlignmentResult:
    media_id = message.media_id
    stem = str(media_id)
    logger.info("Processing media_id=%s", media_id)

    try:
        # Fetch per-entry bucket configuration from DynamoDB
        entry = dynamo_reader.get_entry(media_id)
        if not entry:
            return AlignmentResult(
                source_key=stem,
                success=False,
                error=f"No DynamoDB entry for media_id={media_id}",
            )
        if not entry.media_bucket_s3:
            logger.error("media_bucket_s3 not set for media_id=%s", media_id)
            return AlignmentResult(
                source_key=stem,
                success=False,
                error="media_bucket_s3 not set in DynamoDB entry",
            )

        audio_bucket = entry.media_bucket_s3
        text_bucket = entry.media_fixed_transcribed_bucket
        output_bucket = entry.media_subtitles
        language = entry.language or "he"

        logger.info(
            "media_id=%s audio_bucket=%s text_bucket=%s output_bucket=%s language=%s",
            media_id, audio_bucket, text_bucket, output_bucket, language,
        )

        # Download audio from S3
        audio_path = s3_downloader.download_audio(f"{stem}.mp3", temp_dir, audio_bucket)
        if not audio_path:
            logger.error("Failed to download audio for media_id=%s", media_id)
            return AlignmentResult(
                source_key=stem,
                success=False,
                error="Failed to download audio",
            )

        # Download corrected text from S3
        text_content = s3_downloader.download_text(f"{stem}.txt", text_bucket)
        if not text_content:
            logger.error("Failed to download text for media_id=%s", media_id)
            return AlignmentResult(
                source_key=stem,
                success=False,
                error="Failed to download text",
            )

        # Download pre-fix .time file and run DTW to fix text before alignment
        evaluator = AlignmentEvaluator(
            band_width=config.dtw_band_width or None,
            step_pattern=config.dtw_step_pattern,
            window_type=config.dtw_window_type,
            match_threshold=config.dtw_match_threshold,
            high_dist_threshold=config.dtw_high_dist_threshold,
            low_score_threshold=config.dtw_low_score_threshold,
            jump_threshold=config.dtw_jump_threshold,
            drop_threshold=config.dtw_drop_threshold,
            ma_window=config.dtw_ma_window,
            rolling_avg_target=config.rolling_avg_target,
        )
        if config.dtw_enabled:
            prefix_time_content = s3_downloader.download_text(f"{stem}.pre-fix.time", text_bucket)
            if prefix_time_content:
                text_content = evaluator.pre_alignment_fix(prefix_time_content, text_content)
            else:
                logger.warning("No pre-fix .time file for media_id=%s, skipping DTW fix", media_id)
        else:
            logger.info("DTW disabled, using raw text for media_id=%s", media_id)

        # Align audio with text
        result = align_audio(str(audio_path), text_content, language, config.token_step)
        if result is None:
            logger.error("Alignment failed for media_id=%s", media_id)
            return AlignmentResult(
                source_key=stem,
                success=False,
                error="Alignment returned None",
            )

        # Save outputs locally
        json_path, vtt_path, srt_path = save_outputs(result, temp_dir, stem)

        # Evaluate alignment quality and truncate if degradation detected
        analysis_result = evaluator.post_alignment_evaluate(json_path)
        if analysis_result and analysis_result.get("should_truncate"):
            truncate_point = analysis_result["truncate_point"]
            logger.warning(
                "Degradation detected for media_id=%s: rolling_avg=%d, dtw_cutoff=%s, truncating at %d",
                media_id,
                analysis_result["rolling_avg_method"],
                analysis_result["dtw_cutoff_index"],
                truncate_point,
            )
            truncate_vtt_file(vtt_path, truncate_point)
            truncate_srt_file(srt_path, truncate_point)

        # Upload JSON, VTT, and SRT to S3
        source_audio = f"{stem}.mp3"
        json_uploaded = s3_uploader.upload_file(
            json_path, f"{stem}.json", output_bucket, source_audio=source_audio
        )
        vtt_uploaded = s3_uploader.upload_file(
            vtt_path, f"{stem}.vtt", output_bucket, source_audio=source_audio
        )
        srt_uploaded = s3_uploader.upload_file(
            srt_path, f"{stem}.srt", output_bucket, source_audio=source_audio
        )
        if config.dtw_enabled:
            txt_uploaded = s3_uploader.upload_content(
                text_content, f"{stem}.dtw.txt", output_bucket, source_audio=source_audio
            )
        else:
            txt_uploaded = True

        if not json_uploaded or not vtt_uploaded or not srt_uploaded or not txt_uploaded:
            logger.error("Failed to upload outputs for media_id=%s", media_id)
            return AlignmentResult(
                source_key=stem,
                success=False,
                error="Failed to upload outputs",
            )

        # Upload analysis file only if truncation was applied
        if analysis_result and analysis_result.get("should_truncate"):
            analysis_uploaded = s3_uploader.upload_content(
                json.dumps(analysis_result, indent=2, default=str),
                f"{stem}.analysis",
                output_bucket,
                source_audio=source_audio,
            )
            if not analysis_uploaded:
                logger.error("Failed to upload analysis file for media_id=%s", media_id)

        # Send completion notification to final queue
        sqs_sender.send_completion_message(
            stem=stem,
            source_audio=source_audio,
            vtt_key=f"{stem}.vtt",
            json_key=f"{stem}.json",
        )

        dynamo_reader.set_status(media_id, "aligned")

        logger.info("Successfully processed media_id=%s", media_id)
        return AlignmentResult(
            source_key=stem,
            success=True,
            output_key=f"{stem}.vtt",
        )

    except Exception as e:
        logger.error("Failed to process media_id=%s: %s", media_id, e, exc_info=True)
        return AlignmentResult(
            source_key=stem,
            success=False,
            error=str(e),
        )


def run_worker_loop(
    sqs_receiver: SQSReceiver,
    dynamo_reader: DynamoReader,
    s3_downloader: S3Downloader,
    s3_uploader: S3Uploader,
    sqs_sender: SQSSender,
) -> None:
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
            messages = sqs_receiver.receive_messages(max_messages=1, wait_time=20)

            if not messages:
                logger.debug("No messages received, continuing to poll...")
                continue

            for message in messages:
                result = process_message(
                    message=message,
                    dynamo_reader=dynamo_reader,
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
