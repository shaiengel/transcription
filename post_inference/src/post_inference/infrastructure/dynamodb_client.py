import logging
from typing import Any

logger = logging.getLogger(__name__)


class DynamoDBClient:

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_item(self, table_name: str, key: dict) -> dict | None:
        try:
            response = self._client.get_item(TableName=table_name, Key=key)
            return response.get("Item")
        except Exception as e:
            logger.error("DynamoDB get_item failed: %s", e)
            return None

    def update_item(
        self,
        table_name: str,
        key: dict,
        update_expression: str,
        expression_values: dict,
        expression_names: dict | None = None,
    ) -> bool:
        try:
            kwargs: dict = {
                "TableName": table_name,
                "Key": key,
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_values,
            }
            if expression_names:
                kwargs["ExpressionAttributeNames"] = expression_names
            self._client.update_item(**kwargs)
            return True
        except Exception as e:
            logger.error("DynamoDB update_item failed: %s", e)
            return False
