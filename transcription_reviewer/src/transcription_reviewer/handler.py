"""AWS Lambda handler for transcription review.

Triggered by CloudWatch Alarm when ASG scales to 0, or by webhook
with batch_job_id for Gemini batch result processing.
"""

import json
import logging

import boto3

from transcription_reviewer.config import config
from transcription_reviewer.infrastructure.dependency_injection import (
    DependenciesContainer,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _reinvoke_self(context, event: dict) -> None:
    """Asynchronously re-invoke this Lambda to continue processing remaining files."""
    lambda_client = boto3.client("lambda", region_name=config.aws_region)
    lambda_client.invoke(
        FunctionName=context.function_name,
        InvocationType="Event",
        Payload=json.dumps(event).encode(),
    )
    logger.info("Re-invoked Lambda %s to continue processing", context.function_name)


def lambda_handler(event: dict, context) -> dict:
    """Lambda handler: routes to the appropriate orchestrator based on event shape."""
    logger.info("Received event: %s", json.dumps(event))

    try:
        container = DependenciesContainer()
        config.validate()

        batch_job_id = event.get("batch_job_id") if isinstance(event, dict) else None

        if batch_job_id:
            orchestrator = container.gemini_batch_retrigger_orchestrator(
                batch_job_id=batch_job_id,
                lambda_context=context,
            )
            logger.info("Result trigger: processing batch_job_id=%s", batch_job_id)
        else:
            orchestrator = container.orchestrator()
            logger.info("Initial trigger: using %s", type(orchestrator).__name__)

        result = orchestrator.start()
        response_body = result.to_dict()
        logger.info("Review completed: %s", response_body)

        if result.timed_out:
            # After retrigger finalize, invoke fresh (no batch_job_id) to pick up next batch
            reinvoke_event = {} if batch_job_id else event
            _reinvoke_self(context, reinvoke_event)

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
