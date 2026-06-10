"""Abstract base class for fetching Steinsaltz commentary for a daf."""

from abc import ABC, abstractmethod


class DafTextFetcher(ABC):
    @abstractmethod
    def fetch_for_daf(self, massechet_name: str, daf_id: int) -> str | None:
        """Fetch Steinsaltz commentary for a specific daf.

        Args:
            massechet_name: The Sefaria name for the massechet (e.g. "Bava_Kamma").
            daf_id: The daf number (integer).

        Returns:
            Combined commentary text for amud aleph (and amud bet if it exists),
            or None if not found.
        """
