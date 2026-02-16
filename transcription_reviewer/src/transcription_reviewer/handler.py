"""AWS Lambda handler for transcription review.

Triggered by CloudWatch Alarm when ASG scales to 0.
Reads all *.timed.txt files from S3 and fixes them using Bedrock.
"""

import json
import logging

from transcription_reviewer.config import config
from transcription_reviewer.handlers.review import process_transcriptions
from transcription_reviewer.infrastructure.dependency_injection import (
    DependenciesContainer,
)

# Configure root logger for Lambda (all modules will inherit this)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def lambda_handler(event: dict, context) -> dict:
    """
    Lambda handler function triggered by CloudWatch Alarm.

    Args:
        event: CloudWatch Alarm event data.
        context: Lambda context object.

    Returns:
        Response dict with statusCode and body.
    """
    logger.info("Received CloudWatch Alarm event: %s", json.dumps(event))

    try:
        # Initialize DI container
        container = DependenciesContainer()

        # Get services from container
        s3_reader = container.s3_reader()
        pipeline = container.llm_pipeline()
        transcription_fixer = container.transcription_fixer()

        # Validate configuration
        config.validate()

        pipeline_name = type(pipeline).__name__
        logger.info(f"Using pipeline: {pipeline_name}")

        # Process transcriptions using three-step pipeline
        result = process_transcriptions(
            s3_reader=s3_reader,
            pipeline=pipeline,
            transcription_fixer=transcription_fixer,
            bucket=config.transcription_bucket,
            prefix=config.transcription_prefix,
        )

        response_body = {
            "message": "Transcription review completed",
            "backend": config.llm_backend,
            "total_found": result.total_found,
            "fixed": result.fixed,
            "failed": result.failed,
            "batch_job_arn": result.batch_job_arn,
        }

        logger.info("Review completed: %s", response_body)

        return {
            "statusCode": 200,
            "body": json.dumps(response_body),
        }

    except Exception as e:
        logger.exception("Failed to process transcriptions: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
