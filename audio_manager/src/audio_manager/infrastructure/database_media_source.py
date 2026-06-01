from audio_manager.models.media_source import MediaSource
from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import (
    get_connection,
    get_media_links,
    get_calendar_entries,
    get_chapters_for_daf,
)


class DatabaseMediaSource(MediaSource):
    """Media source that fetches entries from the MSSQL database."""

    def get_media_entries(self, days_ago: int = 0) -> list[MediaEntry]:
        """Fetch media entries from the database.

        Args:
            days_ago: Number of days ago to fetch media entries for. Default is 0 (today).

        Returns:
            List of MediaEntry objects for today's calendar entries.
        """
        with get_connection() as conn:
            calendar_entries = get_calendar_entries(conn, days_ago=days_ago)

            all_media: list[MediaEntry] = []
            for entry in calendar_entries:
                media_list = get_media_links(conn, entry.massechet_id, entry.daf_id)
                chapters = get_chapters_for_daf(entry.massechet_id, entry.daf_id)
                chapter_parts = [
                    f"פרק {ch['chapter_name']} שהוא פרק {ch['chapter_count']}"
                    for ch in chapters
                ]
                if chapter_parts:
                    chapter_str = "\n" + " and also ".join(chapter_parts)
                else:
                    chapter_str = ""
                for media in media_list:
                    media.details = (
                        f"a Talmud Massechet:{media.massechet_name} of Daf: {media.daf_name}"
                        + chapter_str
                    )
                all_media.extend(media_list)

            return all_media
