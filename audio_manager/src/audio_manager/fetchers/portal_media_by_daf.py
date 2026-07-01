import logging
import subprocess
import tempfile
from pathlib import Path

from audio_manager.handlers.media import apply_max_word_split, enrich_with_steinsaltz_by_daf, print_media_links
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
                apply_max_word_split(media_links)

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


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # if len(sys.argv) < 3:
    #     print("Usage: python portal_media_by_daf.py <media_link> <output_path> [file_type]")
    #     print("  file_type: mp3 (default) or mp4")
    #     sys.exit(1)

    # media_link = sys.argv[1]
    # output_path = Path(sys.argv[2])
    # file_type = sys.argv[3] if len(sys.argv) > 3 else "mp3"

    media = MediaEntry(
        media_id="222065",
        media_link="https://files.daf-yomi.com/files/mahor/megila/megila13.mp3",
        file_type="mp3",
    )

    fetcher = PortalMediaByDaf(media_source=None, text_fetcher=None, daf_list=[])
    result = fetcher.download_media(media, Path("C:\\portal\\transcription\\audio_manager\\222065.mp3"))
    print(f"Download {'succeeded' if result else 'failed'}")
    sys.exit(0 if result else 1)
