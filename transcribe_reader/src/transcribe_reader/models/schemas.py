"""Pydantic models for transcribe_reader."""

from pydantic import BaseModel


class VttFile(BaseModel):
    """Represents a VTT file to sync."""

    s3_key: str  # e.g., "154618.vtt"
    filename: str  # e.g., "154618.vtt"
    stem: str  # e.g., "154618"
    content: str | None = None  # VTT content after download
    gitlab_path: str | None = None  # Target path in GitLab
    exists_in_s3: bool = False
