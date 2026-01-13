"""Pydantic models for SQS messages and transcription results."""

from pydantic import BaseModel


class SQSMessage(BaseModel):
    """Message received from SQS queue."""

    s3_key: str
    language: str
    massechet_name: str
    daf_name: str
    receipt_handle: str | None = None


class TranscriptionResult(BaseModel):
    """Result of a transcription operation."""

    source_key: str
    vtt_key: str | None = None
    text_key: str | None = None
    timed_key: str | None = None
    success: bool
