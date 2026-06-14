from abc import ABC, abstractmethod


class AuthenticationError(Exception):
    """Raised when webhook authentication fails."""


class PostProcessing(ABC):

    @abstractmethod
    def authenticate(self, event: dict) -> None:
        """Verify event authenticity. Raises AuthenticationError if invalid."""
        ...

    @abstractmethod
    def process(self, event: dict) -> dict:
        """Execute post-processing. Returns {statusCode, body} response."""
        ...
