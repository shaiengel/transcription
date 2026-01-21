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
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.batch_jsonl import (
    MIN_ENTRIES,
    create_jsonl,
    prepare_batch_entries,
)

logger = logging.getLogger(__name__)

# Environment variables for batch processing
BATCH_BUCKET = os.getenv("TRANSCRIPTION_BUCKET", "portal-daf-yomi-transcription")
BATCH_ROLE_ARN = os.getenv("BATCH_ROLE_ARN", "")
BATCH_MODEL_ID = os.getenv("BATCH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")


@dataclass
class ReviewResult:
    """Result of the transcription review process."""

    total_found: int
    fixed: int
    failed: int
    batch_job_arn: str | None = None


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

    # Create JSONL file locally
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)

    if not create_jsonl(entries, tmp_path):
        logger.error("Failed to create batch JSONL file")
        tmp_path.unlink(missing_ok=True)
        return None

    # Upload to S3
    if not s3_client.upload_file(tmp_path, BATCH_BUCKET, input_key):
        logger.error("Failed to upload batch input to S3")
        tmp_path.unlink(missing_ok=True)
        return None

    tmp_path.unlink(missing_ok=True)
    logger.info("Uploaded batch input to s3://%s/%s", BATCH_BUCKET, input_key)

    # Create batch job
    if not BATCH_ROLE_ARN:
        logger.error("BATCH_ROLE_ARN environment variable not set")
        return None

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
    bucket: str,
    prefix: str,
) -> ReviewResult:
    """
    Find all timed transcriptions and fix them using Bedrock.

    If there are >= 100 entries (after splitting), creates a batch job.
    Otherwise, processes each file individually using invoke_model.

    Args:
        s3_reader: S3Reader service for listing/reading transcriptions.
        transcription_fixer: TranscriptionFixer service for fixing with Bedrock.
        s3_client: S3Client for uploading batch input.
        bedrock_batch_client: BedrockBatchClient for batch job creation.
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

    # Find all timed transcription files
    transcriptions = s3_reader.list_timed_transcriptions(
        bucket=bucket,
        prefix=prefix,
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

        transcription_files.append(
            TranscriptionFile(
                stem=stem,
                content=content,
                system_prompt=system_prompt,
                line_count=line_count,
            )
        )

    logger.info(
        "Loaded %d transcription files, %d failed to load",
        len(transcription_files),
        failed_to_load,
    )

    # Prepare batch entries
    entries = prepare_batch_entries(transcription_files)
    logger.info("Prepared %d batch entries", len(entries))

    # If we have enough entries for batch processing, create and submit batch job
    if len(entries) >= MIN_ENTRIES:
        job_arn = _submit_batch_job(entries, s3_client, bedrock_batch_client)
        if job_arn:
            return ReviewResult(
                total_found=total,
                fixed=0,
                failed=failed_to_load,
                batch_job_arn=job_arn,
            )

    # Fall back to individual processing
    logger.info("Not enough entries for batch processing, using individual invoke_model")

    fixed_count = 0
    failed_count = failed_to_load

    for transcription in transcriptions:
        content = s3_reader.get_transcription_content(transcription)
        if not content:
            continue  # Already counted in failed_to_load

        fixed_content = transcription_fixer.fix_transcription(content, transcription.key)
        if fixed_content:
            logger.info("Fixed and saved VTT: %s", transcription.key)
            fixed_count += 1
        else:
            logger.error("Failed to fix: %s", transcription.key)
            failed_count += 1

    logger.info(
        "Processing complete: %d found, %d fixed, %d failed",
        total,
        fixed_count,
        failed_count,
    )

    return ReviewResult(
        total_found=total,
        fixed=fixed_count,
        failed=failed_count,
    )
