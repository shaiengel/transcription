import json
import logging

from post_inference.infrastructure.dependency_injection import DependenciesContainer
from post_inference.models.post_processing import AuthenticationError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    logger.info("Received event: %s", json.dumps(event))

    container = DependenciesContainer()
    processor = container.post_processor()

    try:
        processor.authenticate(event)
    except AuthenticationError as e:
        logger.warning("Authentication failed: %s", e)
        return {"statusCode": 401, "body": json.dumps({"error": str(e)})}

    try:
        return processor.process(event)
    except Exception as e:
        logger.exception("Failed to process: %s", e)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
