"""WER (Word Error Rate) utility functions for transcription comparison."""

import logging
import re
import string
from dataclasses import dataclass

import jiwer

logger = logging.getLogger(__name__)


@dataclass
class WERResult:
    """Result of WER comparison between reference and hypothesis text."""

    wer: float
    wil: float
    substitutions: int
    deletions: int
    insertions: int
    has_meaningful_changes: bool


def text_canonization(text: str) -> str:
    """Normalize text for WER comparison: remove punctuation, normalize whitespace."""
    translator = str.maketrans("", "", string.punctuation.replace("-", ""))
    translator[ord("-")] = ord(" ")  # Map the hyphen to a space
    text = text.translate(translator)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compute_wer(reference: str, hypothesis: str, threshold: int = 5) -> WERResult:
    """
    Compare reference and hypothesis text using WER metrics.

    Args:
        reference: The original/reference text
        hypothesis: The text to compare against reference
        threshold: Maximum S/D/I count to consider as "no meaningful change"

    Returns:
        WERResult with WER, WIL, and edit operation counts
    """
    ref_normalized = text_canonization(reference)
    hyp_normalized = text_canonization(hypothesis)

    result = jiwer.process_words(ref_normalized, hyp_normalized)

    substitutions = result.substitutions
    deletions = result.deletions
    insertions = result.insertions

    # If total edit operations are below threshold, likely no meaningful change
    total_edits = substitutions + deletions + insertions
    has_meaningful_changes = total_edits >= threshold

    return WERResult(
        wer=result.wer,
        wil=result.wil,
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        has_meaningful_changes=has_meaningful_changes,
    )


def log_wer_result(media_id: str, wer_result: WERResult) -> None:
    """Log WER comparison results."""
    logger.info(
        "WER check for %s: WER=%.4f, WIL=%.4f, S=%d, D=%d, I=%d, meaningful_changes=%s",
        media_id,
        wer_result.wer,
        wer_result.wil,
        wer_result.substitutions,
        wer_result.deletions,
        wer_result.insertions,
        wer_result.has_meaningful_changes,
    )
