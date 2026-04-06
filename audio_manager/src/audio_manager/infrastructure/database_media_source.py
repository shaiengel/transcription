import json
from pathlib import Path

from audio_manager.models.media_source import MediaSource
from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import (
    get_connection,
    get_media_links,
    get_calendar_entries,
)

_MASSECHET_DATA: list[dict] = json.loads(
    (Path(__file__).parent.parent / "massechet_data.json").read_text(encoding="utf-8")
)


def _get_chapters_for_daf(massechet_id: int, daf_id: int) -> list[dict]:
    chapters = [
        c for c in _MASSECHET_DATA
        if c.get("massechet_id") == massechet_id
        and c.get("chapter_start_daf") is not None
    ]
    chapters.sort(key=lambda c: (c["chapter_start_daf"], c["chapter_start_amud"]))

    daf_start = daf_id * 2
    daf_end = daf_id * 2 + 1

    active = []
    for i, ch in enumerate(chapters):
        ch_pos = ch["chapter_start_daf"] * 2 + (ch["chapter_start_amud"] - 1)
        if ch_pos > daf_end:
            break
        next_pos = (
            chapters[i + 1]["chapter_start_daf"] * 2 + (chapters[i + 1]["chapter_start_amud"] - 1)
            if i + 1 < len(chapters)
            else float("inf")
        )
        if next_pos > daf_start:
            active.append(ch)
    return active


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
                chapters = _get_chapters_for_daf(entry.massechet_id, entry.daf_id)
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
