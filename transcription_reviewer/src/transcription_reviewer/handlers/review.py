"""Handler for reviewing transcriptions when ASG scales to zero."""

import logging
from dataclasses import dataclass

from transcription_reviewer.services.s3_reader import S3Reader

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of the transcription review process."""

    total_found: int


def process_transcriptions(
    s3_reader: S3Reader,
    bucket: str,
    prefix: str,
) -> ReviewResult:
    """
    Find all timed transcriptions and return the count.

    Args:
        s3_reader: S3Reader service for listing transcriptions.
        bucket: S3 bucket containing transcriptions.
        prefix: S3 prefix to filter transcriptions.

    Returns:
        ReviewResult with count of files found.
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

    return ReviewResult(total_found=total)
