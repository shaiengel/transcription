"""Pydantic models for transcription reviewer."""

from pathlib import Path

from pydantic import BaseModel


class CloudWatchAlarmEvent(BaseModel):
    """CloudWatch Alarm event when ASG hits 0."""

    alarm_name: str
    alarm_description: str | None = None
    new_state_value: str
    old_state_value: str
    reason: str | None = None


class TimedTranscription(BaseModel):
    """Represents a timed transcription file in S3."""

    bucket: str
    key: str
    filename: str

    @property
    def stem(self) -> str:
        """Return filename without extension."""
        return Path(self.filename).stem

    @property
    def filename_time(self) -> str:
        """Return the .time filename for this transcription."""
        return f"{self.stem}.time"

    @property
    def s3_uri(self) -> str:
        """Return full S3 URI."""
        return f"s3://{self.bucket}/{self.key}"


class TranscriptionFile(BaseModel):
    """A transcription file with metadata for batch processing."""

    stem: str
    content: str
    system_prompt: str
    line_count: int
    word_count: int


class ReviewResult(BaseModel):
    """Result of the transcription review process."""

    total_found: int
    fixed: int
    failed: int
    batch_job_arn: str | None = None
