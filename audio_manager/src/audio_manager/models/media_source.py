from abc import ABC, abstractmethod

from audio_manager.models.schemas import MediaEntry


class MediaSource(ABC):
    """Abstract base class for media sources."""

    @abstractmethod
    def get_media_entries(self) -> list[MediaEntry]:
        """Fetch media entries from the source.

        Returns:
            List of MediaEntry objects representing available media.
        """
        pass
