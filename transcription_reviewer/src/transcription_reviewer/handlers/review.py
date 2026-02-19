"""Handler for reviewing transcriptions when ASG scales to zero."""

import logging
import os

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.time_parser import truncate_content_at_long_segment

logger = logging.getLogger(__name__)

# Buckets for cleanup (still used by _cleanup_source_files)
AUDIO_BUCKET = os.getenv("AUDIO_BUCKET", "portal-daf-yomi-audio")
TRANSCRIPTION_BUCKET = os.getenv("TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")
MAX_SEGMENT_DURATION_SECONDS = 22.0


def _cleanup_source_files(s3_client: S3Client, stem: str) -> None:
    """
    Remove source files after processing.

    Deletes from AUDIO_BUCKET: {stem}.*
    Deletes from TRANSCRIPTION_BUCKET: {stem}.*

    Args:
        s3_client: S3Client for delete operations.
        stem: File stem (filename without extension).
    """
    # Delete all files matching stem.* from audio bucket
    s3_client.delete_objects_by_prefix(AUDIO_BUCKET, f"{stem}.")

    # Delete all files matching stem.* from transcription bucket
    s3_client.delete_objects_by_prefix(TRANSCRIPTION_BUCKET, f"{stem}.")

    logger.info("Cleaned up source files for: %s", stem)


def process_transcriptions(
    s3_reader: S3Reader,
    pipeline: LLMPipeline,
    transcription_fixer: TranscriptionFixer,
    bucket: str,
    prefix: str,
) -> ReviewResult:
    """
    Process transcriptions using three-step pipeline:
    1. prepare_data() - Prepare files for LLM
    2. invoke() - Call LLM
    3. post_process() - Process results

    Args:
        s3_reader: S3Reader service for listing/reading transcriptions.
        pipeline: LLMPipeline implementation (Bedrock or Gemini).
        bucket: S3 bucket containing transcriptions.
        prefix: S3 prefix to filter transcriptions.

    Returns:
        ReviewResult with counts of found, fixed, failed, and optional batch_job_arn.
    """
    logger.info(
        "Processing transcriptions from s3://%s/%s",
        bucket,
        prefix,
    )

    # 1. List transcription files
    transcriptions = s3_reader.list_transcriptions(
        bucket=bucket,
        prefix=prefix,
        suffix=".txt",
    )

    if not transcriptions:
        logger.info("No transcription files found")
        return ReviewResult(total_found=0, fixed=0, failed=0, batch_job_arn=None)

    logger.info("Found %d transcription files", len(transcriptions))

    # 2. Process each file individually through the full pipeline
    fixed_count = 0
    failed_count = 0

    for trans in transcriptions:
        # Load file content
        content = s3_reader.get_transcription_content(trans)
        if not content:
            logger.error("Failed to read: %s", trans.key)
            failed_count += 1
            continue

        # Check for long segments in .time file and truncate if needed
        time_content = s3_reader.get_content_from_bucket(
            trans.filename_time,
            bucket=trans.bucket,
        )
        if time_content:
            content = truncate_content_at_long_segment(
                content=content,
                time_content=time_content,
                max_duration_seconds=MAX_SEGMENT_DURATION_SECONDS,
                stem=trans.stem,
            )

        # Fetch system prompt from S3 template file
        system_prompt = transcription_fixer.get_system_prompt(trans.key)
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
        )

        # Process THIS file through full pipeline before moving to next
        logger.info("Processing file: %s", trans.stem)

        logger.info("  Step 1: Preparing data...")
        prepared_data = pipeline.prepare_data([transcription_file])

        logger.info("  Step 2: Invoking LLM...")
        llm_response = pipeline.invoke(prepared_data)

        logger.info("  Step 3: Post-processing results...")
        result = pipeline.post_process(llm_response, prepared_data)

        # Aggregate counts
        fixed_count += result.fixed
        failed_count += result.failed

        logger.info("  Completed: fixed=%d, failed=%d", result.fixed, result.failed)

    return ReviewResult(
        total_found=len(transcriptions),
        fixed=fixed_count,
        failed=failed_count,
        batch_job_arn=None,
    )
