import logging
from typing import Any

logger = logging.getLogger(__name__)


class DynamoDBClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def put_item(self, table_name: str, item: dict) -> bool:
        try:
            self._client.put_item(TableName=table_name, Item=item)
            return True
        except Exception as e:
            logger.error("DynamoDB put_item failed: %s", e)
            return False
