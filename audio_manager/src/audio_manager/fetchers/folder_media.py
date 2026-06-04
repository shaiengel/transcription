import logging
import shutil
from pathlib import Path

from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


class FolderMedia(MediaFetcher):
    """Fetches media from a local folder.

    Expected folder layout:
        {stem}.mp3           - audio file
        {stem}.template.txt  - system prompt context for this recording
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def get_all_medias(self) -> list[MediaEntry]:
        media_list: list[MediaEntry] = []
        for mp3_path in sorted(self._folder.glob("*.mp3")):
            stem = mp3_path.stem
            template_path = self._folder / f"{stem}.template.txt"
            if not template_path.exists():
                logger.warning("No template file for %s, skipping", mp3_path.name)
                continue
            template_content = template_path.read_text(encoding="utf-8")
            media_list.append(
                MediaEntry(
                    media_id=stem,
                    media_link=str(mp3_path),
                    file_type="mp3",
                    language="hebrew",
                    details=stem,
                    steinsaltz=template_content,
                    source="folder",
                )
            )
        logger.info("Found %d media files in %s", len(media_list), self._folder)
        return media_list

    def download_media(self, media: MediaEntry, path: Path) -> bool:
        src = Path(media.media_link)
        if not src.exists():
            logger.error("Source file not found: %s", src)
            return False
        shutil.copy2(src, path)
        return path.exists() and path.stat().st_size > 0
