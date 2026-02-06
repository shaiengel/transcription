"""AWS Lambda handler for post-inference processing.

Triggered by EventBridge when a Bedrock batch inference job completes.
Reads batch output, matches .time files, creates VTT, uploads to final bucket.
"""

import json
import logging

from post_inference.config import config
from post_inference.handlers.process import process_batch_output
from post_inference.infrastructure.dependency_injection import DependenciesContainer
from post_inference.services.batch_result_processor import BatchResultProcessor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """Lambda handler triggered by EventBridge batch job completion.

    Args:
        event: EventBridge event with batch job ARN.
        context: Lambda context object.

    Returns:
        Response dict with statusCode and body.
    """
    logger.info("Received event: %s", json.dumps(event))

    try:
        # Extract job ARN from EventBridge event
        detail = event.get("detail", {})
        job_arn = detail.get("batchJobArn")

        if not job_arn:
            logger.error("No batchJobArn in event detail")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No batchJobArn in event"}),
            }

        logger.info("Processing batch job: %s", job_arn)

        # Initialize DI container
        container = DependenciesContainer()

        s3_client = container.s3_client()
        sqs_client = container.sqs_client()
        bedrock_client = container.bedrock_boto_client()
        batch_result_processor = BatchResultProcessor(s3_client)

        # Process batch output
        result = process_batch_output(
            job_arn=job_arn,
            bedrock_client=bedrock_client,
            s3_client=s3_client,
            sqs_client=sqs_client,
            batch_result_processor=batch_result_processor,
            transcription_bucket=config.transcription_bucket,
            output_bucket=config.output_bucket,
            audio_bucket=config.audio_bucket,
            sqs_queue_url=config.sqs_queue_url,
        )

        response_body = {
            "message": "Post-inference processing completed",
            "total_records": result.total_records,
            "processed": result.processed,
            "failed": result.failed,
            "cleaned_up": result.cleaned_up,
        }

        logger.info("Processing completed: %s", response_body)

        return {
            "statusCode": 200,
            "body": json.dumps(response_body),
        }

    except Exception as e:
        logger.exception("Failed to process batch output: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
