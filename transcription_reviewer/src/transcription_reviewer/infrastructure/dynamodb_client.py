import logging
from typing import Any

from botocore.exceptions import ClientError

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

    def update_item(self, table_name: str, key: dict, update_expression: str, expression_values: dict, expression_names: dict | None = None) -> bool:
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

    def put_item(self, table_name: str, item: dict) -> bool:
        try:
            self._client.put_item(TableName=table_name, Item=item)
            return True
        except Exception as e:
            logger.error("DynamoDB put_item failed: %s", e)
            return False

    def put_item_conditional(self, table_name: str, item: dict, condition: str) -> bool:
        """Put item with a condition expression.

        Returns True if the item was written, False if the condition check failed.
        Raises on any other error.
        """
        try:
            self._client.put_item(
                TableName=table_name,
                Item=item,
                ConditionExpression=condition,
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            logger.error("DynamoDB put_item_conditional failed: %s", e)
            raise

    def delete_item(self, table_name: str, key: dict) -> bool:
        try:
            self._client.delete_item(TableName=table_name, Key=key)
            return True
        except Exception as e:
            logger.error("DynamoDB delete_item failed: %s", e)
            return False
