"""Plain text formatter implementation."""

from gpu_instance.models.formatter import Formatter, SegmentData


class TextFormatter(Formatter):
    """Formats transcription segments as plain text."""

    @property
    def extension(self) -> str:
        return ".txt"

    def format(self, segments: list[SegmentData]) -> str:
        return "\n".join(segment.text for segment in segments)
