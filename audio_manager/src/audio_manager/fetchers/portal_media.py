import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

from audio_manager.handlers.media import (
    enrich_with_steinsaltz,
    get_calendar_window,
    print_media_links,
)
from audio_manager.services.downloader import download_file, extract_audio_from_mp4
from audio_manager.infrastructure import DatabaseMediaSource
from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry
from audio_manager.models.daf_text_fetcher import DafTextFetcher

logger = logging.getLogger(__name__)


class PortalMedia(MediaFetcher):
    """Fetches media from the Portal database."""

    def __init__(self, media_source, text_fetcher: DafTextFetcher | None) -> None:
        self._media_source = media_source
        self._text_fetcher = text_fetcher

    def get_all_medias(self) -> list[MediaEntry]:
        day_offsets: list[tuple[str, int]] = [
            # ("yesterday", 1),
            ("today", 0),
            # ("tomorrow", -1),
        ]

        all_media: list[MediaEntry] = []

        for day_label, days_ago in day_offsets:
            target_date = date.today() - timedelta(days=days_ago)
            logger.info("")
            logger.info("=" * 50)
            logger.info(
                "Processing %s (%s, days_ago=%d)",
                day_label,
                target_date.isoformat(),
                days_ago,
            )
            logger.info("=" * 50)

            media_links: list[MediaEntry] = self._media_source.get_media_entries(
                days_ago=days_ago
            )

            for m in media_links:
                m.source = "portal"

            if isinstance(self._media_source, DatabaseMediaSource):
                calendar = get_calendar_window(days_ago=days_ago)
                enrich_with_steinsaltz(media_links, calendar, self._text_fetcher)

            print_media_links(media_links)
            all_media.extend(media_links)

        return all_media

    def download_media(self, media: MediaEntry, path: Path) -> bool:
        if media.file_type != "mp4":
            if not download_file(media.media_link, path):
                logger.warning("Download failed for media_id=%s", media.media_id)
                return False
            return True

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp4:
            mp4_path = Path(tmp4.name)
        try:
            if not download_file(media.media_link, mp4_path):
                logger.warning("Download failed for media_id=%s", media.media_id)
                return False
            if not extract_audio_from_mp4(mp4_path, path):
                logger.warning("Audio extraction failed for media_id=%s", media.media_id)
                return False
            return True
        finally:
            mp4_path.unlink(missing_ok=True)
