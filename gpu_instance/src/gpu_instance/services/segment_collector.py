"""Collect transcription segments from whisper model output."""

import logging
from typing import Any, Iterator

from gpu_instance.models.formatter import SegmentData
from gpu_instance.services.utils import format_timestamp


def collect_segments(segments: Iterator[Any] | None) -> list[SegmentData]:
    """
    Collect all segments from the iterator into a list.

    Args:
        segments: Iterator of segment objects from faster-whisper, or None.

    Returns:
        List of SegmentData objects, or empty list on failure.
    """
    if segments is None:
        logging.error("Cannot collect segments: segments iterator is None")
        return []

    try:
        collected = []
        for i, segment in enumerate(segments, start=1):
            text = segment.text.strip()
            start = format_timestamp(segment.start)
            end = format_timestamp(segment.end)
            logging.info(f"[{i}] {start} - {end}: {text}")
            collected.append(
                SegmentData(
                    index=i,
                    start=segment.start,
                    end=segment.end,
                    text=text,
                )
            )
        return collected

    except Exception as e:
        logging.error(f"Failed to collect segments: {e}", exc_info=True)
        return []
