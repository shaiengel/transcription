"""Handler for processing batch inference output."""

import logging

from post_inference.infrastructure.s3_client import S3Client
from post_inference.infrastructure.sqs_client import SQSClient
from post_inference.models.schemas import ProcessResult
from post_inference.services.batch_result_processor import (
    BatchResultProcessor,
    group_split_records,
    parse_batch_output,
)

logger = logging.getLogger(__name__)


def _get_output_s3_uri(bedrock_client, job_arn: str) -> str | None:
    """Get the output S3 URI from a completed batch job.

    Args:
        bedrock_client: boto3 bedrock client.
        job_arn: ARN of the batch job.

    Returns:
        Output S3 URI prefix, or None if failed.
    """
    try:
        response = bedrock_client.get_model_invocation_job(jobIdentifier=job_arn)
        output_config = response.get("outputDataConfig", {})
        s3_config = output_config.get("s3OutputDataConfig", {})
        return s3_config.get("s3Uri")
    except Exception as e:
        logger.error("Failed to get job details for %s: %s", job_arn, e)
        return None


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse s3://bucket/prefix into (bucket, prefix)."""
    without_scheme = s3_uri.removeprefix("s3://")
    bucket, _, prefix = without_scheme.partition("/")
    return bucket, prefix


def process_batch_output(
    job_arn: str,
    bedrock_client,
    s3_client: S3Client,
    sqs_client: SQSClient,
    batch_result_processor: BatchResultProcessor,
    transcription_bucket: str,
    output_bucket: str,
    audio_bucket: str,
    sqs_queue_url: str,
) -> ProcessResult:
    """Process completed batch inference output.

    1. Get output S3 URI from batch job
    2. Find and parse the output JSONL
    3. Group split records by stem
    4. For each stem: match .time file, create VTT, upload, notify SQS, cleanup

    Args:
        job_arn: ARN of the completed batch job.
        bedrock_client: boto3 bedrock client.
        s3_client: S3Client for S3 operations.
        sqs_client: SQSClient for SQS operations.
        batch_result_processor: Processor for individual records.
        transcription_bucket: Bucket with .time files and batch output.
        output_bucket: Destination bucket for .vtt files.
        audio_bucket: Audio bucket for cleanup.
        sqs_queue_url: SQS queue URL for notifications.

    Returns:
        ProcessResult with counts.
    """
    # Get output location from batch job
    output_s3_uri = _get_output_s3_uri(bedrock_client, job_arn)
    if not output_s3_uri:
        logger.error("Could not get output S3 URI for job: %s", job_arn)
        return ProcessResult(total_records=0, processed=0, failed=0, cleaned_up=0)

    logger.info("Batch output location: %s", output_s3_uri)

    # Parse S3 URI and find the output JSONL file
    bucket, prefix = _parse_s3_uri(output_s3_uri)
    objects = s3_client.list_objects(bucket=bucket, prefix=prefix, suffix=".jsonl.out")

    if not objects:
        logger.error("No .jsonl.out file found at %s", output_s3_uri)
        return ProcessResult(total_records=0, processed=0, failed=0, cleaned_up=0)

    # Read the output JSONL (there should be one file)
    output_key = objects[0]["Key"]
    logger.info("Reading batch output: s3://%s/%s", bucket, output_key)

    jsonl_content = s3_client.get_object_content(bucket, output_key)
    if not jsonl_content:
        logger.error("Failed to read batch output file")
        return ProcessResult(total_records=0, processed=0, failed=0, cleaned_up=0)

    # Parse and group records
    records = parse_batch_output(jsonl_content)
    merged = group_split_records(records)

    total = len(merged)
    processed = 0
    failed = 0
    cleaned_up = 0

    logger.info("Processing %d stems from %d records", total, len(records))

    for stem, fixed_text in merged.items():
        success = batch_result_processor.process_record(
            stem=stem,
            fixed_text=fixed_text,
            transcription_bucket=transcription_bucket,
            output_bucket=output_bucket,
        )

        if success:
            processed += 1

            # Notify SQS
            vtt_filename = f"{stem}.vtt"
            sqs_client.send_message(sqs_queue_url, {"filename": vtt_filename})

            # Copy .time file as pre-fix transcription to output bucket before cleanup
            time_key = f"{stem}.time"
            pre_fix_key = f"{stem}.pre-fix.time"
            s3_client.copy_object(transcription_bucket, time_key, output_bucket, pre_fix_key)

            # Cleanup source files
            s3_client.delete_objects_by_prefix(audio_bucket, f"{stem}.")
            s3_client.delete_objects_by_prefix(transcription_bucket, f"{stem}.")
            cleaned_up += 1
            logger.info("Cleaned up source files for: %s", stem)
        else:
            failed += 1

    logger.info(
        "Processing complete: %d processed, %d failed, %d cleaned up",
        processed,
        failed,
        cleaned_up,
    )

    return ProcessResult(
        total_records=total,
        processed=processed,
        failed=failed,
        cleaned_up=cleaned_up,
    )
