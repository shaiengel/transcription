"""Utilities for parsing and truncating .time format files."""

import logging
import re

logger = logging.getLogger(__name__)


def parse_timestamp(ts: str) -> float:
    """Parse timestamp 'HH:MM:SS.mmm' to seconds."""
    parts = ts.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def find_long_segment_index(
    time_content: str, max_duration_seconds: float = 22.0
) -> int | None:
    """Find line index of first segment with duration > max_duration_seconds.

    Args:
        time_content: Content of .time file.
        max_duration_seconds: Maximum allowed segment duration.

    Returns:
        Line index (0-based) or None if no long segment found.
    """
    lines = time_content.strip().split("\n")

    for i, line in enumerate(lines):
        # Format: [index] start - end: text
        match = re.match(r"\[\d+\]\s+(\d+:\d+:\d+\.\d+)\s+-\s+(\d+:\d+:\d+\.\d+):", line)
        if match:
            start = parse_timestamp(match.group(1))
            end = parse_timestamp(match.group(2))
            duration = end - start
            if duration > max_duration_seconds:
                return i

    return None


def truncate_at_line(content: str, line_index: int) -> str:
    """Truncate content to keep only lines 0..line_index-1."""
    lines = content.strip().split("\n")
    return "\n".join(lines[:line_index])


def truncate_content_at_long_segment(
    content: str,
    time_content: str,
    max_duration_seconds: float = 22.0,
    stem: str = "",
) -> str:
    """Truncate content if a long segment is found in time_content.

    Args:
        content: The transcription content to truncate.
        time_content: The .time file content to analyze.
        max_duration_seconds: Maximum allowed segment duration.
        stem: File stem for logging.

    Returns:
        Truncated content if long segment found, original content otherwise.
    """
    long_segment_idx = find_long_segment_index(time_content, max_duration_seconds)
    if long_segment_idx is not None:
        logger.warning(
            "Long segment (>%.0fs) at line %d for %s, truncating",
            max_duration_seconds,
            long_segment_idx,
            stem,
        )
        return truncate_at_line(content, long_segment_idx)
    return content
