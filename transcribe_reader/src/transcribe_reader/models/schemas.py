"""Pydantic models for transcribe_reader."""

from pydantic import BaseModel


class TranscriptionFile(BaseModel):
    """Represents a transcription file to sync."""

    s3_key: str  # e.g., "154618.vtt"
    filename: str  # e.g., "154618.vtt"
    stem: str  # e.g., "154618"
    source_bucket: str | None = None  # Optional bucket override for this file
    content: str | None = None  # File content after download
    gitlab_path: str | None = None  # Target path in GitLab
    exists_in_s3: bool = False
