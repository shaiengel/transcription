"""Pydantic models for SQS messages and alignment results."""

from pydantic import BaseModel


class SQSMessage(BaseModel):
    """Message received from SQS queue."""

    s3_key: str
    language: str = "he"
    receipt_handle: str | None = None


class AlignmentResult(BaseModel):
    """Result of an alignment operation."""

    source_key: str
    success: bool
    output_key: str | None = None
    error: str | None = None
