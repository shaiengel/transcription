from abc import ABC, abstractmethod

from audio_manager.models.schemas import MediaEntry


class MediaSource(ABC):
    """Abstract base class for media sources."""

    @abstractmethod
    def get_media_entries(self, days_ago: int = 0) -> list[MediaEntry]:
        """Fetch media entries from the source.

        Args:
            days_ago: Number of days ago to fetch media entries for. Default is 0 (today).

        Returns:
            List of MediaEntry objects representing available media.
        """
        pass
