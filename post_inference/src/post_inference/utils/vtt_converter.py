"""VTT format converter for timed transcriptions."""

import re


def convert_to_vtt(content: str) -> str:
    """Convert timed transcription to VTT format.

    Input format: [1] 00:00:00.960 - 00:00:04.340: text
    Output format: WEBVTT with cue blocks
    """
    lines = content.strip().split("\n")
    vtt_lines = ["WEBVTT", ""]

    pattern = re.compile(
        r"^\[(\d+)\]\s+(\d{2}:\d{2}:\d{2}\.\d{3})\s+-\s+(\d{2}:\d{2}:\d{2}\.\d{3}):\s*(.*)$"
    )

    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            num, start, end, text = match.groups()
            vtt_lines.append(num)
            vtt_lines.append(f"{start} --> {end}")
            vtt_lines.append(text)
            vtt_lines.append("")

    return "\n".join(vtt_lines)
