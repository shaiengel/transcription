"""Handler for reviewing transcriptions when ASG scales to zero."""

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from transcription_reviewer.infrastructure.bedrock_batch_client import BedrockBatchClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.schemas import TranscriptionFile
from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.token_counter import TokenCounter
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.batch_jsonl import (
    create_jsonl,
    prepare_batch_entries,
)

logger = logging.getLogger(__name__)

# Environment variables for batch processing
BATCH_BUCKET = os.getenv("TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")
BATCH_ROLE_ARN = os.getenv("BATCH_ROLE_ARN", "")
BATCH_MODEL_ID = os.getenv("BATCH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")

# Buckets for cleanup
AUDIO_BUCKET = os.getenv("AUDIO_BUCKET", "portal-daf-yomi-audio")
TRANSCRIPTION_BUCKET = os.getenv("TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")


@dataclass
class ReviewResult:
    """Result of the transcription review process."""

    total_found: int
    fixed: int
    failed: int
    batch_job_arn: str | None = None


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


def _submit_batch_job(
    entries: list,
    s3_client: S3Client,
    bedrock_batch_client: BedrockBatchClient,
) -> str | None:
    """
    Create and submit a batch inference job.

    Args:
        entries: List of BatchEntry objects.
        s3_client: S3Client for uploading batch input.
        bedrock_batch_client: BedrockBatchClient for batch job creation.

    Returns:
        Job ARN if successful, None otherwise.
    """
    job_id = str(uuid.uuid4())[:8]
    job_name = f"transcription-fix-{job_id}"
    input_key = f"batch-input/{job_name}.jsonl"
    output_prefix = f"batch-output/{job_name}/"

    # Check role before doing any work
    if not BATCH_ROLE_ARN:
        logger.error("BATCH_ROLE_ARN environment variable not set")
        return None

    # Create JSONL file and upload to S3
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / f"{job_name}.jsonl"

        stats = create_jsonl(entries, tmp_path)
        logger.info(
            "Batch stats: %d real entries, %d dummy entries, %d tokens",
            stats["real_entries"],
            stats["dummy_entries"],
            stats["real_tokens"],
        )

        # Upload to S3
        if not s3_client.upload_file(tmp_path, BATCH_BUCKET, input_key):
            logger.error("Failed to upload batch input to S3")
            return None

    logger.info("Uploaded batch input to s3://%s/%s", BATCH_BUCKET, input_key)

    # Create batch job
    job_arn = bedrock_batch_client.create_batch_job(
        job_name=job_name,
        model_id=BATCH_MODEL_ID,
        role_arn=BATCH_ROLE_ARN,
        input_s3_uri=f"s3://{BATCH_BUCKET}/{input_key}",
        output_s3_uri=f"s3://{BATCH_BUCKET}/{output_prefix}",
    )

    if job_arn:
        logger.info("Created batch job: %s", job_arn)
    else:
        logger.error("Failed to create batch job")

    return job_arn


def process_transcriptions(
    s3_reader: S3Reader,
    transcription_fixer: TranscriptionFixer,
    s3_client: S3Client,
    bedrock_batch_client: BedrockBatchClient,
    token_counter: TokenCounter,
    bucket: str,
    prefix: str,
) -> ReviewResult:
    """
    Find all timed transcriptions and fix them using Bedrock batch inference.

    Always uses batch processing (padded to 100 entries for batch pricing).
    Files exceeding token limits are automatically split.

    Args:
        s3_reader: S3Reader service for listing/reading transcriptions.
        transcription_fixer: TranscriptionFixer service for fixing with Bedrock.
        s3_client: S3Client for uploading batch input.
        bedrock_batch_client: BedrockBatchClient for batch job creation.
        token_counter: TokenCounter service for counting tokens.
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

    # Find all transcription files
    transcriptions = s3_reader.list_transcriptions(
        bucket=bucket,
        suffix=".txt",
    )

    total = len(transcriptions)
    logger.info("Found %d timed transcription files", total)

    if not transcriptions:
        return ReviewResult(total_found=0, fixed=0, failed=0)

    # Collect all files with content and system prompts
    transcription_files: list[TranscriptionFile] = []
    failed_to_load = 0

    for transcription in transcriptions:
        content = s3_reader.get_transcription_content(transcription)
        if not content:
            logger.error("Failed to read: %s", transcription.key)
            failed_to_load += 1
            continue

        system_prompt = transcription_fixer.get_system_prompt(transcription.key)
        if not system_prompt:
            logger.error("Failed to get system prompt for: %s", transcription.key)
            failed_to_load += 1
            continue

        stem = transcription_fixer._get_stem(transcription.key)
        line_count = len(content.strip().split("\n"))
        word_count = len(content.split())

        transcription_files.append(
            TranscriptionFile(
                stem=stem,
                content=content,
                system_prompt=system_prompt,
                line_count=line_count,
                word_count=word_count,
            )
        )

    logger.info(
        "Loaded %d transcription files, %d failed to load",
        len(transcription_files),
        failed_to_load,
    )

    # Prepare batch entries (always pads to 100 for batch pricing)
    entries = prepare_batch_entries(transcription_files, token_counter)
    real_entries = [e for e in entries if not e.record_id.startswith("dummy_")]
    logger.info(
        "Prepared %d batch entries (%d real, %d padding)",
        len(entries),
        len(real_entries),
        len(entries) - len(real_entries),
    )

    # Always use batch processing (entries are padded to 100)
    job_arn = _submit_batch_job(entries, s3_client, bedrock_batch_client)
    if job_arn:
        return ReviewResult(
            total_found=total,
            fixed=0,
            failed=failed_to_load,
            batch_job_arn=job_arn,
        )

    # Batch job creation failed
    logger.error("Failed to create batch job")
    return ReviewResult(
        total_found=total,
        fixed=0,
        failed=total,
    )
