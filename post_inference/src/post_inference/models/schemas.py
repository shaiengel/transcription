"""Pydantic models for post-inference processing."""

from pydantic import BaseModel


class BatchOutputRecord(BaseModel):
    """A single record from Bedrock batch output JSONL."""

    record_id: str
    fixed_text: str


class ProcessResult(BaseModel):
    """Result of processing batch output."""

    total_records: int
    processed: int
    failed: int
    cleaned_up: int
