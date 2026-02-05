"""Pydantic models for transcribe_reader."""

from pydantic import BaseModel


class CalendarEntry(BaseModel):
    """Calendar entry from database."""

    massechet_id: int
    daf_id: int


class MediaInfo(BaseModel):
    """Media information from View_Media table."""

    media_id: int
    maggid_description: str | None = None
    massechet_name: str | None = None
    daf_name: str | None = None
    language: str | None = None


class VttFile(BaseModel):
    """Represents a VTT file to sync."""

    media_id: int
    s3_key: str  # e.g., "12345.vtt"
    content: str | None = None  # VTT content after download
    gitlab_path: str | None = None  # Target path in GitLab
    exists_in_s3: bool = False
    # Metadata for commit description
    maggid_description: str | None = None
    massechet_name: str | None = None
    daf_name: str | None = None
    language: str | None = None

    @property
    def description(self) -> str:
        """Build a description string from metadata."""
        parts = []
        if self.massechet_name:
            parts.append(self.massechet_name)
        if self.daf_name:
            parts.append(self.daf_name)
        if self.maggid_description:
            parts.append(f"({self.maggid_description})")
        if self.language:
            parts.append(f"[{self.language}]")
        return " ".join(parts) if parts else self.s3_key
