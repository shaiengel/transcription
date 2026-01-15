"""Handler for reviewing transcriptions when ASG scales to zero."""

import logging
from dataclasses import dataclass

from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of the transcription review process."""

    total_found: int
    fixed: int
    failed: int


def process_transcriptions(
    s3_reader: S3Reader,
    transcription_fixer: TranscriptionFixer,
    bucket: str,
    prefix: str,
) -> ReviewResult:
    """
    Find all timed transcriptions and fix them using Bedrock.

    Args:
        s3_reader: S3Reader service for listing/reading transcriptions.
        transcription_fixer: TranscriptionFixer service for fixing with Bedrock.
        bucket: S3 bucket containing transcriptions.
        prefix: S3 prefix to filter transcriptions.

    Returns:
        ReviewResult with counts of found, fixed, and failed.
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

    fixed_count = 0
    failed_count = 0

    for transcription in transcriptions:
        logger.info("Processing: %s", transcription.key)

        # Read content from S3
        content = s3_reader.get_transcription_content(transcription)
        if not content:
            logger.error("Failed to read: %s", transcription.key)
            failed_count += 1
            continue

        # Fix using Bedrock and save VTT to S3
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
