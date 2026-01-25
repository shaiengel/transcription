from pathlib import Path

from pydantic import BaseModel


class CalendarEntry(BaseModel):
    massechet_id: int
    daf_id: int


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
