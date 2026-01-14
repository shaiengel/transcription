"""Timed text formatter implementation."""

from gpu_instance.models.formatter import Formatter, SegmentData
from gpu_instance.services.utils import format_timestamp


class TimedTextFormatter(Formatter):
    """Formats transcription segments with timestamps."""

    @property
    def extension(self) -> str:
        return ".timed.txt"

    def format(self, segments: list[SegmentData]) -> str:
        lines = []
        for segment in segments:
            start = format_timestamp(segment.start)
            end = format_timestamp(segment.end)
            lines.append(f"[{segment.index}] {start} - {end}: {segment.text}")
        return "\n".join(lines)
