from abc import ABC, abstractmethod
from pathlib import Path

from audio_manager.models.schemas import MediaEntry


class MediaFetcher(ABC):
    @abstractmethod
    def get_all_medias(self) -> list[MediaEntry]: ...

    @abstractmethod
    def download_media(self, media: MediaEntry, path: Path) -> bool: ...
