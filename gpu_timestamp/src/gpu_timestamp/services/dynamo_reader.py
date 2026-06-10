import logging

from gpu_timestamp.infrastructure.dynamodb_client import DynamoDBClient
from gpu_timestamp.models.dynamo_entry import DynamoDBMediaEntry

logger = logging.getLogger(__name__)


class DynamoReader:
    def __init__(self, dynamodb_client: DynamoDBClient, table_name: str):
        self._client = dynamodb_client
        self._table_name = table_name

    def set_status(self, media_id: int, status: str) -> bool:
        return self._client.update_item(
            table_name=self._table_name,
            key={"media_id": {"S": str(media_id)}},
            update_expression="SET #s = :s",
            expression_names={"#s": "status"},
            expression_values={":s": {"S": status}},
        )

    def get_entry(self, media_id: int) -> DynamoDBMediaEntry | None:
        item = self._client.get_item(
            table_name=self._table_name,
            key={"media_id": {"S": str(media_id)}},
        )
        if not item:
            logger.error("No DynamoDB entry found for media_id=%s", media_id)
            return None
        try:
            return DynamoDBMediaEntry.from_dynamo_item(item)
        except Exception as e:
            logger.error("Failed to parse DynamoDB entry for media_id=%s: %s", media_id, e)
            return None
