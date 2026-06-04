import logging
import tempfile
from pathlib import Path

from audio_manager.handlers.media import enrich_with_steinsaltz_by_daf, print_media_links
from audio_manager.models.daf_text_fetcher import DafTextFetcher
from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import (
    get_connection,
    get_massechet_by_english_name,
    get_media_links,
)
from audio_manager.services.downloader import download_file, extract_audio_from_mp4

logger = logging.getLogger(__name__)


class PortalMediaByDaf(MediaFetcher):
    """Fetches media from the Portal database for an explicit list of (massechet, daf) pairs.

    massechet_english must match the massechet_english field in massechet_data.json.
    daf_id is the numeric daf number (e.g. 2 for daf bet).
    """

    def __init__(
        self,
        media_source,
        text_fetcher: DafTextFetcher | None,
        daf_list: list[tuple[str, int]],
    ) -> None:
        self._media_source = media_source
        self._text_fetcher = text_fetcher
        self._daf_list = daf_list

    def get_all_medias(self) -> list[MediaEntry]:
        all_media: list[MediaEntry] = []

        with get_connection() as conn:
            for massechet_english, daf_id in self._daf_list:
                entry = get_massechet_by_english_name(massechet_english)
                if not entry:
                    logger.warning(
                        "No massechet_data entry found for massechet_english=%r", massechet_english
                    )
                    continue

                massechet_id = entry.get("massechet_id")
                if massechet_id is None:
                    logger.warning(
                        "massechet_id is null for massechet_english=%r", massechet_english
                    )
                    continue

                logger.info("Fetching media for %s daf %d", massechet_english, daf_id)
                media_links = get_media_links(conn, massechet_id, daf_id)

                for m in media_links:
                    m.source = "portal"

                all_media.extend(media_links)

        enrich_with_steinsaltz_by_daf(all_media, self._text_fetcher)
        print_media_links(all_media)
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
