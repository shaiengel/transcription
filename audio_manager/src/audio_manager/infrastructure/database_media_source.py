from audio_manager.models.media_source import MediaSource
from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import (
    get_connection,
    get_media_links,
    get_today_calendar_entries,
)


class DatabaseMediaSource(MediaSource):
    """Media source that fetches entries from the MSSQL database."""

    def get_media_entries(self) -> list[MediaEntry]:
        """Fetch today's media entries from the database.

        Returns:
            List of MediaEntry objects for today's calendar entries.
        """
        with get_connection() as conn:
            calendar_entries = get_today_calendar_entries(conn)

            all_media: list[MediaEntry] = []
            for entry in calendar_entries:
                media_list = get_media_links(conn, entry.massechet_id, entry.daf_id)
                all_media.extend(media_list)

            for media in all_media:
                media.details = f"a Talmud Massechet:{media.massechet_name} of Daf: {media.daf_name}"
            return all_media
