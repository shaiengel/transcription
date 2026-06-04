"""DynamoDB-backed tracker for per-file LLM fix progress and distributed locking."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from transcription_reviewer.infrastructure.dynamodb_client import DynamoDBClient

logger = logging.getLogger(__name__)

@dataclass
class FixTrackerEntry:
    media_id: str
    retry_number: int
    lambda_started_at: str          # ISO-8601 UTC
    lambda_time_remaining_ms: int
    current_chunk: int
    total_chunks: int

    def elapsed_seconds(self) -> float:
        started = datetime.fromisoformat(self.lambda_started_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - started).total_seconds()

    @classmethod
    def from_dynamo_item(cls, item: dict) -> "FixTrackerEntry":
        return cls(
            media_id=item["media_id"]["S"],
            retry_number=int(item["retry_number"]["N"]),
            lambda_started_at=item["lambda_started_at"]["S"],
            lambda_time_remaining_ms=int(item["lambda_time_remaining_ms"]["N"]),
            current_chunk=int(item.get("current_chunk", {}).get("N", "1")),
            total_chunks=int(item.get("total_chunks", {}).get("N", "1")),
        )

    def to_dynamo_item(self) -> dict:
        return {
            "media_id": {"S": self.media_id},
            "retry_number": {"N": str(self.retry_number)},
            "lambda_started_at": {"S": self.lambda_started_at},
            "lambda_time_remaining_ms": {"N": str(self.lambda_time_remaining_ms)},
            "current_chunk": {"N": str(self.current_chunk)},
            "total_chunks": {"N": str(self.total_chunks)},
        }


class FixTrackerService:
    """Manages the transcription-fix-tracker DynamoDB table.

    Provides atomic file claiming, crash detection, per-chunk progress tracking,
    and cross-lambda retry counting.
    """

    def __init__(self, dynamodb_client: DynamoDBClient, table_name: str) -> None:
        self._client = dynamodb_client
        self._table_name = table_name

    def try_claim(
        self,
        media_id: str,
        time_remaining_ms: int,
        lambda_started_at: str,
    ) -> tuple[bool, FixTrackerEntry | None]:
        """Atomically claim a file for processing.

        Returns:
            (True, None)              — file was newly claimed by this lambda
            (False, existing_entry)  — file is already tracked; caller must decide
        """
        entry = FixTrackerEntry(
            media_id=media_id,
            retry_number=1,
            lambda_started_at=lambda_started_at,
            lambda_time_remaining_ms=time_remaining_ms,
            current_chunk=1,
            total_chunks=1,
        )
        claimed = self._client.put_item_conditional(
            table_name=self._table_name,
            item=entry.to_dynamo_item(),
            condition="attribute_not_exists(media_id)",
        )
        if claimed:
            logger.info("Claimed %s in fix tracker", media_id)
            return True, None

        existing = self._get_entry(media_id)
        return False, existing

    def _get_entry(self, media_id: str) -> FixTrackerEntry | None:
        item = self._client.get_item(
            table_name=self._table_name,
            key={"media_id": {"S": media_id}},
        )
        if not item:
            return None
        try:
            return FixTrackerEntry.from_dynamo_item(item)
        except Exception as e:
            logger.error("Failed to parse fix tracker entry for %s: %s", media_id, e)
            return None

    def update_progress(
        self,
        media_id: str,
        current_chunk: int,
        total_chunks: int,
        time_remaining_ms: int,
    ) -> bool:
        return self._client.update_item(
            table_name=self._table_name,
            key={"media_id": {"S": media_id}},
            update_expression="SET current_chunk = :cc, total_chunks = :tc, lambda_time_remaining_ms = :tr",
            expression_values={
                ":cc": {"N": str(current_chunk)},
                ":tc": {"N": str(total_chunks)},
                ":tr": {"N": str(time_remaining_ms)},
            },
        )

    def update_retry(
        self,
        media_id: str,
        retry_number: int,
        time_remaining_ms: int,
        lambda_started_at: str,
    ) -> bool:
        """Update tracker when a new lambda is taking over a crashed file."""
        return self._client.update_item(
            table_name=self._table_name,
            key={"media_id": {"S": media_id}},
            update_expression=(
                "SET retry_number = :r, lambda_time_remaining_ms = :tr, "
                "lambda_started_at = :sa, current_chunk = :cc"
            ),
            expression_values={
                ":r": {"N": str(retry_number)},
                ":tr": {"N": str(time_remaining_ms)},
                ":sa": {"S": lambda_started_at},
                ":cc": {"N": "1"},
            },
        )

    def delete_entry(self, media_id: str) -> bool:
        return self._client.delete_item(
            table_name=self._table_name,
            key={"media_id": {"S": media_id}},
        )
