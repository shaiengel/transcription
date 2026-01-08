"""Format transcription segments to WebVTT format."""

import logging
import os
from typing import Any, Iterator


def format_timestamp(seconds: float) -> str:
    """
    Convert seconds to VTT timestamp format (HH:MM:SS.mmm).

    Args:
        seconds: Time in seconds.

    Returns:
        Formatted timestamp string.
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def segments_to_vtt(segments: Iterator[Any]) -> str:
    """
    Convert faster-whisper segments to VTT format.

    Args:
        segments: Iterator of segment objects from faster-whisper.
                  Each segment has: start, end, text attributes.

    Returns:
        Complete VTT file content as string.
    """
    lines = ["WEBVTT", ""]
    logging.info("WEBVTT")
    logging.info("")

    for i, segment in enumerate(segments, start=1):
        start_ts = format_timestamp(segment.start)
        end_ts = format_timestamp(segment.end)
        text = segment.text.strip()

        msg = f"{str(i)}"
        logging.info(msg)
        lines.append(msg)

        msg = f"{start_ts} --> {end_ts}"
        logging.info(msg)
        lines.append(msg)

        msg = f"{text}"
        logging.info(msg)
        lines.append(msg) 

        logging.info("")       
        lines.append("")

    return "\n".join(lines)


def save_vtt(vtt_content: str, audio_path: str, temp_dir: str) -> str:
    """
    Save VTT content to file.

    Args:
        vtt_content: The VTT formatted string.
        audio_path: Path to original audio file (used to derive output path).
        temp_dir: Directory to save the VTT file.

    Returns:
        Path to saved VTT file.
    """
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    vtt_path = os.path.join(temp_dir, f"{base_name}.vtt")

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write(vtt_content)

    return vtt_path
