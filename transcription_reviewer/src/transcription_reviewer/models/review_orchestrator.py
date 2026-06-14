"""Abstract orchestrator and result types for transcription review."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class StartResult:
    """Result returned by any orchestrator's start() method."""

    mode: str = "sync"
    total_found: int = 0
    fixed: int = 0
    failed: int = 0
    timed_out: bool = False
    batch_job_arn: str | None = None
    job_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_found": self.total_found,
            "fixed": self.fixed,
            "failed": self.failed,
            "timed_out": self.timed_out,
            "batch_job_arn": self.batch_job_arn,
            "job_ref": self.job_ref,
        }


class ReviewOrchestrator(ABC):
    """Abstract base for all transcription review orchestrators."""

    @abstractmethod
    def start(self) -> StartResult:
        """Execute the full processing lifecycle."""
        ...
