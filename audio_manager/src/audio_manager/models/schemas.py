from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel


class CalendarEntry(BaseModel):
    massechet_id: int
    daf_id: int


@dataclass
class CalendarWindow:
    today: list[CalendarEntry] = field(default_factory=list)
    yesterday: list[CalendarEntry] = field(default_factory=list)
    tomorrow: list[CalendarEntry] = field(default_factory=list)


class MediaEntry(BaseModel):
    media_id: int | str
    media_link: str
    maggid_description: str | None = None
    massechet_name: str | None = None
    daf_name: str | None = None
    details: str | None = None
    language: str | None = None
    media_duration: int | None = None
    file_type: str | None = None
    downloaded_path: Path | None = None  # Set after download
    steinsaltz: str | None = None  # Steinsaltz commentary from Sefaria
    audio_bucket: str | None = None
    context_files_bucket: str | None = None
    transcription_bucket: str | None = None
    fixed_transcription_bucket: str | None = None
    subtitles_bucket: str | None = None
    source: str | None = None
