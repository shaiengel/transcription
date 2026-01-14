"""Abstract formatter base class and segment data model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SegmentData:
    """Stores relevant data from a transcription segment."""

    index: int
    start: float
    end: float
    text: str


class Formatter(ABC):
    """Abstract base class for text formatters."""

    @property
    @abstractmethod
    def extension(self) -> str:
        """File extension for this format (e.g., '.vtt', '.txt')."""
        pass

    @abstractmethod
    def format(self, segments: list[SegmentData]) -> str:
        """Convert segments to formatted string."""
        pass
