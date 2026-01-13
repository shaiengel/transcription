"""AWS Lambda handler for transcription review."""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Lambda handler function.

    Args:
        event: Lambda event data.
        context: Lambda context object.

    Returns:
        Response dict with statusCode and body.
    """
    logger.info("Received event: %s", json.dumps(event))

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Hello World"}),
    }
