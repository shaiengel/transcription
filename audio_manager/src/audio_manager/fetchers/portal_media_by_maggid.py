import logging
from pathlib import Path

from audio_manager.fetchers.portal_media_by_daf import _validate_and_fix_mp3
from audio_manager.handlers.media import (
    apply_max_word_split,
    enrich_with_steinsaltz_by_daf,
    print_media_links,
)
from audio_manager.models.daf_text_fetcher import DafTextFetcher
from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import get_connection, get_media_links_by_maggid
from audio_manager.services.downloader import download_file, extract_audio_from_mp4

logger = logging.getLogger(__name__)


class PortalMediaByMaggid(MediaFetcher):
    """Fetches all media from the Portal database for a specific maggid_id."""

    def __init__(
        self,
        media_source,
        text_fetcher: DafTextFetcher | None,
        maggid_id: int,
    ) -> None:
        self._media_source = media_source
        self._text_fetcher = text_fetcher
        self._maggid_id = maggid_id

    def get_all_medias(self) -> list[MediaEntry]:
        with get_connection() as conn:
            logger.info("Fetching all media for maggid_id=%d", self._maggid_id)
            media_links = get_media_links_by_maggid(conn, self._maggid_id)

        apply_max_word_split(media_links)
        for m in media_links:
            m.source = "portal"

        enrich_with_steinsaltz_by_daf(media_links, self._text_fetcher)
        print_media_links(media_links)
        return media_links

    def download_media(self, media: MediaEntry, path: Path) -> bool:
        if media.file_type != "mp4":
            if not download_file(media.media_link, path):
                logger.warning("Download failed for media_id=%s", media.media_id)
                return False
            return _validate_and_fix_mp3(path)

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp4:
            mp4_path = Path(tmp4.name)
        try:
            if not download_file(media.media_link, mp4_path):
                logger.warning("Download failed for media_id=%s", media.media_id)
                return False
            if not extract_audio_from_mp4(mp4_path, path):
                logger.warning("Audio extraction failed for media_id=%s", media.media_id)
                return False
            return _validate_and_fix_mp3(path)
        finally:
            mp4_path.unlink(missing_ok=True)
