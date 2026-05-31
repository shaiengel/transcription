import logging
import os
from datetime import datetime, timezone

from audio_manager.infrastructure.dynamodb_client import DynamoDBClient
from audio_manager.models.dynamo_entry import DynamoDBMediaEntry
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


class MediaRegistry:
    def __init__(self, dynamodb_client: DynamoDBClient) -> None:
        self._client = dynamodb_client
        self._table_name = os.getenv("MEDIA_REGISTRY_NAME", "")

    def set_db_entry(self, media_list: list[MediaEntry]) -> None:
        if not self._table_name:
            logger.warning("MEDIA_REGISTRY_NAME not set, skipping tracking")
            return

        for media in media_list:
            if not (media.downloaded_path and media.downloaded_path.exists()):
                continue

            entry = DynamoDBMediaEntry(
                media_id=media.media_id,
                media_url=media.media_link,
                media_bucket_s3=media.audio_bucket,
                context_files_bucket_s3=media.context_files_bucket or "",
                media_transcribed_bucket=media.transcription_bucket or "",
                media_fixed_transcribed_bucket=media.fixed_transcription_bucket or "",
                media_subtitles=media.subtitles_bucket or "",
                status="queued",
                created_at=datetime.now(timezone.utc).isoformat(),
                language=media.language,
                details=media.details,
                massechet_name=media.massechet_name,
                daf_name=media.daf_name,
                maggid_description=media.maggid_description,
                media_duration=media.media_duration,
            )

            if self._client.put_item(self._table_name, entry.to_dynamo_item()):
                logger.info("Tracked media_id=%s in DynamoDB", media.media_id)
