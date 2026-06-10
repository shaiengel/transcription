"""Abstract LLM pipeline for transcription processing."""

from abc import ABC, abstractmethod
from typing import Any

from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile


class LLMPipeline(ABC):
    """Abstract pipeline for LLM-based transcription processing."""

    @abstractmethod
    def prepare_data(self, files: list[TranscriptionFile], context=None) -> Any:
        """
        Prepare transcription files for LLM processing.

        Args:
            files: List of transcription files to prepare.
            context: Lambda context object (used by some pipelines for timeout-aware tracking).

        Returns:
            Prepared data in format suitable for invoke().
        """
        pass

    @abstractmethod
    def invoke(self, prepared_data: Any, context=None, **kwargs) -> Any:
        """
        Invoke LLM with prepared data.

        Args:
            prepared_data: Data from prepare_data().
            context: Lambda context object for timeout checks.
            **kwargs: Pipeline-specific options (e.g. tracker_entry for GeminiPipeline).

        Returns:
            Response from LLM.
        """
        pass

    @abstractmethod
    def post_process(self, llm_response: Any, original_files: list[TranscriptionFile]) -> ReviewResult:
        """
        Post-process LLM response.

        Args:
            llm_response: Response from invoke().
            original_files: Original transcription files.

        Returns:
            ReviewResult with counts and status.
        """
        pass
