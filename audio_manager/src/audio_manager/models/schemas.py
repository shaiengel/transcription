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
    media_id: int
    media_link: str
    maggid_description: str | None
    massechet_name: str | None
    daf_name: str | None
    details: str | None
    language: str | None
    media_duration: int | None
    file_type: str | None
    downloaded_path: Path | None = None  # Set after download
    steinsaltz: str | None = None  # Steinsaltz commentary from Sefaria
