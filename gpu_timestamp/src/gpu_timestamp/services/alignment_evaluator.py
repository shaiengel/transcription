"""Alignment quality evaluation service for detecting degradation in transcriptions."""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def extract_words_from_stable_whisper(json_path: Path) -> list[dict]:
    """Extract word data from stable_whisper JSON output.

    Args:
        json_path: Path to the stable_whisper JSON file.

    Returns:
        List of word dictionaries with word, start_time, end_time, probability.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    words = []
    for segment in data.get("segments", []):
        for word_data in segment.get("words", []):
            words.append({
                "word": word_data.get("word", "").strip(),
                "start_time": word_data.get("start"),
                "end_time": word_data.get("end"),
                "probability": word_data.get("probability"),
            })

    return words


def _compute_moving_average(probs: np.ndarray, window: int = 100) -> np.ndarray:
    """Compute moving average with given window size."""
    moving_avg = np.full(len(probs), np.nan)
    for i in range(len(probs)):
        start = max(0, i - window + 1)
        moving_avg[i] = np.mean(probs[start : i + 1])
    return moving_avg


def detect_degradation_rolling_avg(
    probs: np.ndarray,
    window: int = 100,
    threshold_pct: float = 0.5,
) -> int:
    """Find index where rolling avg drops below threshold_pct of baseline.

    Args:
        probs: Array of word probabilities.
        window: Window size for moving average.
        threshold_pct: Threshold as percentage of baseline.

    Returns:
        Index where degradation detected, or -1 if not detected.
    """
    if len(probs) < window * 3:
        logger.debug("Not enough data points for rolling avg analysis")
        return -1

    moving_avg = _compute_moving_average(probs, window)
    baseline = np.mean(moving_avg[window : window * 3])  # Use indices 100-300 as baseline
    threshold = baseline * threshold_pct

    for i in range(window, len(moving_avg)):
        if moving_avg[i] < threshold:
            return i
    return -1


def detect_degradation_cusum(
    probs: np.ndarray,
    window: int = 100,
    threshold: float = 80.0,
) -> int:
    """CUSUM change point detection for detecting mean shift.

    Args:
        probs: Array of word probabilities.
        window: Window size for baseline calculation.
        threshold: CUSUM threshold for detection.

    Returns:
        Index where degradation detected, or -1 if not detected.
    """
    if len(probs) < window * 3:
        logger.debug("Not enough data points for CUSUM analysis")
        return -1

    moving_avg = _compute_moving_average(probs, window)
    target = np.mean(moving_avg[window : window * 3])  # Baseline target

    cusum_neg = 0

    for i in range(window, len(moving_avg)):
        diff = moving_avg[i] - target
        cusum_neg = min(0, cusum_neg + diff)

        if abs(cusum_neg) > threshold:
            return i
    return -1


def truncate_vtt_file(file_path: Path, word_index: int) -> None:
    """Truncate VTT file at the segment containing word_index.

    VTT format: WEBVTT header, then segments (timestamp, text) separated by blank lines.

    Args:
        file_path: Path to the VTT file.
        word_index: Word index where degradation starts (0-based).
    """
    content = file_path.read_text(encoding="utf-8")
    parts = content.strip().split("\n\n")

    # First part is "WEBVTT" header
    output_parts = [parts[0]]
    cumulative_words = 0

    for segment in parts[1:]:
        lines = segment.split("\n")
        # lines[0] = timestamp, lines[1:] = text
        text = " ".join(lines[1:]) if len(lines) > 1 else ""
        words_in_segment = len(text.split())
        if cumulative_words + words_in_segment >= word_index:
            break
        output_parts.append(segment)
        cumulative_words += words_in_segment

    file_path.write_text("\n\n".join(output_parts) + "\n", encoding="utf-8")
    logger.info("Truncated VTT at word %d (kept %d words)", word_index, cumulative_words)


def truncate_srt_file(file_path: Path, word_index: int) -> None:
    """Truncate SRT file at the segment containing word_index.

    SRT format: segments (index, timestamp, text) separated by blank lines.

    Args:
        file_path: Path to the SRT file.
        word_index: Word index where degradation starts (0-based).
    """
    content = file_path.read_text(encoding="utf-8")
    segments = content.strip().split("\n\n")

    output_segments = []
    cumulative_words = 0

    for segment in segments:
        lines = segment.split("\n")
        # lines[0] = index, lines[1] = timestamp, lines[2:] = text
        text = " ".join(lines[2:]) if len(lines) > 2 else ""
        words_in_segment = len(text.split())
        if cumulative_words + words_in_segment >= word_index:
            break
        output_segments.append(segment)
        cumulative_words += words_in_segment

    file_path.write_text("\n\n".join(output_segments) + "\n", encoding="utf-8")
    logger.info("Truncated SRT at word %d (kept %d words)", word_index, cumulative_words)


def evaluate_alignment(json_path: Path) -> dict | None:
    """Evaluate alignment quality and detect degradation.

    Args:
        json_path: Path to the stable_whisper JSON file.

    Returns:
        Dictionary with analysis results if both methods detect issues,
        None otherwise.
    """
    try:
        words = extract_words_from_stable_whisper(json_path)

        if not words:
            logger.warning("No words found in JSON file: %s", json_path)
            return None

        # Sort words by start_time (JSON is not sorted)
        words.sort(key=lambda w: w["start_time"] if w["start_time"] is not None else 0)

        # Extract probabilities, filtering out None values
        probs = [w["probability"] for w in words if w["probability"] is not None]

        if len(probs) < 300:  # Need at least window * 3 points
            logger.debug(
                "Not enough probability data for analysis: %d words", len(probs)
            )
            return None

        probs_array = np.array(probs)

        rolling_avg_index = detect_degradation_rolling_avg(probs_array)
        cusum_index = detect_degradation_cusum(probs_array)

        logger.info(
            "Degradation detection: rolling_avg=%d, cusum=%d",
            rolling_avg_index,
            cusum_index,
        )

        # Only return analysis if BOTH methods detect issues
        if rolling_avg_index != -1 and cusum_index != -1:
            return {
                "rolling_avg_method": rolling_avg_index,
                "cusum_method": cusum_index,
            }

        return None

    except Exception as e:
        logger.error(
            "Error evaluating alignment for %s: %s", json_path, e, exc_info=True
        )
        return None
