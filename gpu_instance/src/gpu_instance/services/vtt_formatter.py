"""Format transcription segments to WebVTT and plain text formats."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from gpu_instance.services.utils import format_timestamp


@dataclass
class SegmentData:
    """Stores relevant data from a transcription segment."""
    index: int
    start: float
    end: float
    text: str


def collect_segments(segments: Iterator[Any]) -> list[SegmentData]:
    """
    Collect all segments from the iterator into a list.

    Args:
        segments: Iterator of segment objects from faster-whisper.

    Returns:
        List of SegmentData objects.
    """
    collected = []
    for i, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        start = format_timestamp(segment.start)
        end = format_timestamp(segment.end)
        logging.info(f"[{i}] {start} - {end}: {text}")
        collected.append(SegmentData(
            index=i,
            start=segment.start,
            end=segment.end,
            text=text
        ))
    return collected


def segments_to_vtt(segments: list[SegmentData]) -> str:
    """
    Convert collected segments to VTT format.

    Args:
        segments: List of SegmentData objects.

    Returns:
        Complete VTT file content as string.
    """
    lines = ["WEBVTT", ""]    

    for segment in segments:
        start_ts = format_timestamp(segment.start)
        end_ts = format_timestamp(segment.end)

        msg = f"{segment.index}"
        lines.append(msg)

        msg = f"{start_ts} --> {end_ts}"
        lines.append(msg)

        msg = f"{segment.text}"
        lines.append(msg)

        lines.append("")

    return "\n".join(lines)


def segments_to_text(segments: list[SegmentData]) -> str:
    """
    Convert collected segments to plain text format.

    Args:
        segments: List of SegmentData objects.

    Returns:
        Plain text content with all segment texts joined.
    """
    return "\n".join(segment.text for segment in segments)


def segments_to_timed_text(segments: list[SegmentData]) -> str:
    """
    Convert collected segments to timed text format.

    Format: [index] start - end: text

    Args:
        segments: List of SegmentData objects.

    Returns:
        Timed text content with timestamps.
    """
    lines = []
    for segment in segments:
        start = format_timestamp(segment.start)
        end = format_timestamp(segment.end)
        lines.append(f"[{segment.index}] {start} - {end}: {segment.text}")
    return "\n".join(lines)


def save_vtt(vtt_content: str, audio_path: str, temp_dir: str) -> Path:
    """
    Save VTT content to file.

    Args:
        vtt_content: The VTT formatted string.
        audio_path: Path to original audio file (used to derive output path).
        temp_dir: Directory to save the VTT file.

    Returns:
        Path to saved VTT file.
    """
    vtt_path = Path(temp_dir) / Path(audio_path).with_suffix(".vtt").name

    vtt_path.write_text(vtt_content, encoding="utf-8")

    return vtt_path


def save_text(text_content: str, audio_path: str, temp_dir: str) -> Path:
    """
    Save plain text content to file.

    Args:
        text_content: The plain text string.
        audio_path: Path to original audio file (used to derive output path).
        temp_dir: Directory to save the text file.

    Returns:
        Path to saved text file.
    """
    text_path = Path(temp_dir) / Path(audio_path).with_suffix(".txt").name

    text_path.write_text(text_content, encoding="utf-8")

    return text_path


def save_timed_text(timed_content: str, audio_path: str, temp_dir: str) -> Path:
    """
    Save timed text content to file.

    Args:
        timed_content: The timed text string.
        audio_path: Path to original audio file (used to derive output path).
        temp_dir: Directory to save the file.

    Returns:
        Path to saved timed text file.
    """
    audio_name = Path(audio_path).stem
    timed_path = Path(temp_dir) / f"{audio_name}.timed.txt"

    timed_path.write_text(timed_content, encoding="utf-8")

    return timed_path
