import json
import logging

from post_inference.config import config
from post_inference.infrastructure.dynamodb_client import DynamoDBClient
from post_inference.models.post_processing import AuthenticationError, PostProcessing
from post_inference.services.jwt_verifier import JWTVerifier

logger = logging.getLogger(__name__)


class GeminiPostProcessing(PostProcessing):

    def __init__(
        self,
        jwt_verifier: JWTVerifier,
        dynamodb_client: DynamoDBClient,
        lambda_client,
    ):
        self._jwt_verifier = jwt_verifier
        self._dynamodb_client = dynamodb_client
        self._lambda_client = lambda_client

    def authenticate(self, event: dict) -> None:
        headers = event.get("headers", {})
        token = headers.get("webhook-signature") or headers.get("Webhook-Signature")
        if not token:
            raise AuthenticationError("Missing Webhook-Signature header")

        try:
            self._jwt_verifier.verify(token)
        except Exception as e:
            raise AuthenticationError(f"JWT verification failed: {e}") from e

    def process(self, event: dict) -> dict:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        event_type = body.get("type")
        if event_type != "batch.succeeded":
            logger.info("Ignoring webhook event type: %s", event_type)
            return {"statusCode": 200, "body": json.dumps({"message": "ignored"})}

        data = body.get("data", {})
        batch_job_id = data.get("id")
        output_file_uri = data.get("output_file_uri")

        if not batch_job_id:
            logger.warning("No batch_job_id in webhook payload")
            return {"statusCode": 200, "body": json.dumps({"message": "no batch_job_id"})}

        logger.info("Webhook received for batch_job_id: %s", batch_job_id)

        table_name = config.batch_jobs_table
        item = self._dynamodb_client.get_item(
            table_name=table_name,
            key={"batch_job_id": {"S": batch_job_id}},
        )

        if not item:
            logger.info("Unknown batch_job_id: %s — ignoring", batch_job_id)
            return {"statusCode": 200, "body": json.dumps({"message": "unknown job"})}

        current_status = item.get("status", {}).get("S")
        if current_status == "finished":
            logger.info("batch_job_id %s already finished — idempotent skip", batch_job_id)
            return {"statusCode": 200, "body": json.dumps({"message": "already processed"})}

        success = self._dynamodb_client.update_item(
            table_name=table_name,
            key={"batch_job_id": {"S": batch_job_id}},
            update_expression="SET #status = :finished, output_file_uri = :uri",
            expression_values={
                ":finished": {"S": "finished"},
                ":uri": {"S": output_file_uri or ""},
            },
            expression_names={"#status": "status"},
        )

        if not success:
            logger.error("Failed to update DynamoDB for batch_job_id: %s", batch_job_id)
            return {"statusCode": 200, "body": json.dumps({"message": "update failed"})}

        try:
            self._lambda_client.invoke(
                FunctionName=config.reviewer_function_name,
                InvocationType="Event",
                Payload=json.dumps({"batch_job_id": batch_job_id}),
            )
            logger.info("Invoked reviewer for batch_job_id: %s", batch_job_id)
        except Exception as e:
            logger.error("Failed to invoke reviewer Lambda: %s", e)

        return {"statusCode": 200, "body": json.dumps({"message": "processed"})}
