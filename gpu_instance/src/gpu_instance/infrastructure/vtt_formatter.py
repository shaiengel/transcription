"""VTT (WebVTT) formatter implementation."""

from gpu_instance.models.formatter import Formatter, SegmentData
from gpu_instance.services.utils import format_timestamp


class VttFormatter(Formatter):
    """Formats transcription segments as WebVTT subtitles."""

    @property
    def extension(self) -> str:
        return ".vtt"

    def format(self, segments: list[SegmentData]) -> str:
        lines = ["WEBVTT", ""]
        for segment in segments:
            start_ts = format_timestamp(segment.start)
            end_ts = format_timestamp(segment.end)
            lines.append(str(segment.index))
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(segment.text)
            lines.append("")
        return "\n".join(lines)
