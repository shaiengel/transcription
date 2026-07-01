import logging
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from audio_manager.handlers.media import (
    apply_max_word_split,
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


def _mp3_has_issues(mp3_path: Path) -> bool:
    """Run mp3val.exe and return True if there are errors or warnings."""
    try:
        result = subprocess.run(
            ["mp3val.exe", str(mp3_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        # mp3val reports issues with WARNING or ERROR in output
        return "WARNING" in output or "ERROR" in output
    except FileNotFoundError as e:
        logger.error("not found in PATH: %s", e)
        return False


def _validate_and_fix_mp3(mp3_path: Path) -> bool:
    """Validate MP3 file and re-encode with ffmpeg if needed. Returns False if unfixable."""
    if not _mp3_has_issues(mp3_path):
        return True

    logger.warning("MP3 validation issues detected for %s, attempting re-encode", mp3_path)
    temp_path = mp3_path.parent / "temp_reencoded.mp3"

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), str(temp_path)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error("ffmpeg re-encode failed for %s", mp3_path)
            temp_path.unlink(missing_ok=True)
            return False

        if _mp3_has_issues(temp_path):
            logger.error("Re-encoded MP3 still has issues, dropping %s", mp3_path)
            temp_path.unlink(missing_ok=True)
            mp3_path.unlink(missing_ok=True)
            return False

        # Replace original with fixed file
        mp3_path.unlink(missing_ok=True)
        temp_path.rename(mp3_path)
        logger.info("Successfully re-encoded %s", mp3_path)
        return True

    except FileNotFoundError as e:
        logger.error("ffmpeg not found in PATH: %s", e)
        temp_path.unlink(missing_ok=True)
        return False


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
            apply_max_word_split(media_links)

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
            return _validate_and_fix_mp3(path)

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
