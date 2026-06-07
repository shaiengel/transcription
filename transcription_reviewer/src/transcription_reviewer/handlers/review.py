"""Handler for reviewing transcriptions when ASG scales to zero."""

import logging

from transcription_reviewer.config import config
from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.services.dynamo_reader import DynamoReader
from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.time_parser import truncate_content_at_long_segment

logger = logging.getLogger(__name__)


def _is_running_out_of_time(context) -> bool:
    """Check if Lambda has less than the configured threshold of time remaining."""
    if context is None:
        return False
    remaining_ms = context.get_remaining_time_in_millis()
    threshold = config.timeout_threshold_ms
    if remaining_ms < threshold:
        logger.warning(
            "Running low on time: %d ms remaining (threshold: %d ms)",
            remaining_ms,
            threshold,
        )
        return True
    return False


def process_transcriptions(
    s3_reader: S3Reader,
    pipeline: LLMPipeline,
    transcription_fixer: TranscriptionFixer,
    dynamo_reader: DynamoReader,
    bucket: str,
    context=None,
) -> ReviewResult:
    """Process transcriptions using three-step pipeline: prepare_data → invoke → post_process."""
    logger.info("Processing transcriptions from s3://%s", bucket)

    transcriptions = s3_reader.list_transcriptions(
        bucket=bucket,
        prefix="",
        suffix=".txt",
    )

    if not transcriptions:
        logger.info("No transcription files found")
        return ReviewResult(total_found=0, fixed=0, failed=0, batch_job_arn=None)

    logger.info("Found %d transcription files", len(transcriptions))

    fixed_count = 0
    failed_count = 0
    timed_out = False

    for trans in transcriptions:
        try:
            if _is_running_out_of_time(context):
                timed_out = True
                logger.info(
                    "Stopping early due to time limit. Processed %d/%d files.",
                    fixed_count + failed_count,
                    len(transcriptions),
                )
                break

            media_id = trans.stem

            # --- DynamoDB media entry lookup ---
            dynamo_entry = dynamo_reader.get_entry(media_id)
            if not dynamo_entry:
                logger.error("No DynamoDB entry for media_id=%s", media_id)
                failed_count += 1
                continue

            if dynamo_entry.status != "transcribed":
                logger.info(
                    "Skipping %s: status=%s (expected 'transcribed')",
                    trans.stem,
                    dynamo_entry.status,
                )
                continue

            # --- Load file content ---
            content = s3_reader.get_transcription_content(trans)
            if not content:
                logger.error("Failed to read: %s", trans.key)
                failed_count += 1
                continue

            # Truncate at long segments if a .time file is present
            # time_content = s3_reader.get_content_from_bucket(
            #     trans.filename_time,
            #     bucket=trans.bucket,
            # )
            # if time_content:
            #     content = truncate_content_at_long_segment(
            #         content=content,
            #         time_content=time_content,
            #         max_duration_seconds=config.max_segment_duration_seconds,
            #         stem=trans.stem,
            #     )

            # --- Fetch system prompt ---
            system_prompt = transcription_fixer.get_system_prompt(
                trans.key,
                template_bucket=dynamo_entry.context_files_bucket_s3,
            )
            if not system_prompt:
                logger.error("Failed to get system prompt for: %s", trans.key)
                failed_count += 1
                continue

            line_count = len(content.strip().split("\n"))
            word_count = len(content.split())

            transcription_file = TranscriptionFile(
                stem=trans.stem,
                content=content,
                system_prompt=system_prompt,
                line_count=line_count,
                word_count=word_count,
                transcription_bucket=dynamo_entry.media_transcribed_bucket,
                output_bucket=dynamo_entry.media_fixed_transcribed_bucket,
                context_files_bucket=dynamo_entry.context_files_bucket_s3,
            )

            # --- Run full pipeline for this file ---
            logger.info("Processing file: %s", trans.stem)

            logger.info("  Step 1: Preparing data...")
            prepared_data = pipeline.prepare_data([transcription_file], context=context)
            if not prepared_data:
                logger.info("  Skipping %s (tracker signalled skip)", media_id)
                continue

            logger.info("  Step 2: Invoking LLM...")
            llm_response = pipeline.invoke(prepared_data, context=context)

            logger.info("  Step 3: Post-processing results...")
            result = pipeline.post_process(llm_response, prepared_data)

            fixed_count += result.fixed
            failed_count += result.failed

            if result.fixed > 0:
                dynamo_reader.set_status(media_id, "fixed")

            logger.info("  Completed: fixed=%d, failed=%d", result.fixed, result.failed)

        except Exception:
            logger.exception("Unexpected error processing %s", trans.key)
            failed_count += 1

    return ReviewResult(
        total_found=len(transcriptions),
        fixed=fixed_count,
        failed=failed_count,
        batch_job_arn=None,
        timed_out=timed_out,
    )
