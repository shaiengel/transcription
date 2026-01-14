import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from audio_manager.models.media_source import MediaSource
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


class LocalDiskMediaSource(MediaSource):
    """Media source that reads entries from a local directory.

    Environment variables:
        LOCAL_MEDIA_DIR: Path to the directory containing media files.
        LOCAL_MEDIA_LANGUAGE: Language to assign to all entries (default: "hebrew").
        LOCAL_DETAILS: Details/description for all entries (default: "local media").
    """

    def __init__(
        self,
        media_dir: Path | None = None,
        language: str | None = None,
        details: str | None = None,
    ):
        """Initialize the local disk media source.

        Args:
            media_dir: Path to the directory containing media files.
                       If None, reads from LOCAL_MEDIA_DIR env var.
            language: Language to assign to all entries.
                      If None, reads from LOCAL_MEDIA_LANGUAGE env var.
            details: Details/description for all entries.
                     If None, reads from LOCAL_DETAILS env var.
        """
        load_dotenv()
        self.media_dir = media_dir or Path(
            os.getenv("LOCAL_MEDIA_DIR", "./media")
        )
        self.language = language or os.getenv("LOCAL_MEDIA_LANGUAGE", "hebrew")
        self.details = details or os.getenv("LOCAL_DETAILS", "a Talmud Massechet")

    def get_media_entries(self) -> list[MediaEntry]:
        """Scan the local directory for media files.

        Returns:
            List of MediaEntry objects for each mp3/mp4 file found.
            The downloaded_path is already set (no download needed).
        """
        if not self.media_dir.exists():
            logger.warning("Media directory does not exist: %s", self.media_dir)
            return []

        if not self.media_dir.is_dir():
            logger.warning("Media path is not a directory: %s", self.media_dir)
            return []

        entries: list[MediaEntry] = []
        extensions = {".mp3", ".mp4"}

        for file_path in sorted(self.media_dir.iterdir()):
            if file_path.suffix.lower() not in extensions:
                continue

            if not file_path.is_file():
                continue

            # Generate a unique ID from the filename
            media_id = int(
                hashlib.md5(file_path.name.encode()).hexdigest()[:8], 16
            )

            entry = MediaEntry(
                media_id=media_id,
                media_link=file_path.as_uri(),  # file:// URI
                maggid_description=file_path.stem,  # Use filename as description
                massechet_name=None,
                daf_name=None,
                details=self.details,
                language=self.language,
                media_duration=None,
                file_type=file_path.suffix.lstrip(".").lower(),
                downloaded_path=file_path,  # Already local, no download needed
            )
            entries.append(entry)
            logger.debug("Found local media: %s", file_path.name)

        logger.info("Found %d media files in %s", len(entries), self.media_dir)
        return entries
