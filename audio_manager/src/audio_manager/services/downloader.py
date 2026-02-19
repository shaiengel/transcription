import logging
import platform
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def download_file(url: str, dest_path: Path) -> bool:
    """Download a file from URL to destination path."""
    if platform.system() == "Windows":
        return _download_with_httpx(url, dest_path)
    return _download_with_wget(url, dest_path)


def _download_with_httpx(url: str, dest_path: Path) -> bool:
    """Download file using httpx (works on Windows with modern TLS)."""
    try:
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            dest_path.write_bytes(response.content)
        return True
    except Exception as e:
        logger.error("Failed to download %s: %s", url, e)
        return False


def _download_with_wget(url: str, dest_path: Path) -> bool:
    """Download file using wget (works on Linux)."""
    cmd = [
        "wget",
        "-O", str(dest_path),
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "--referer=https://files.daf-yomi.com/",
        url
    ]
    return subprocess.run(cmd).returncode == 0


def extract_audio_from_mp4(mp4_path: Path, mp3_path: Path) -> bool:
    """Extract audio from mp4 to mp3 using ffmpeg."""
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", str(mp4_path),
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                "-y",  # Overwrite output file if exists
                str(mp3_path),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Failed to extract audio from %s: %s", mp4_path, e.stderr.decode())
        return False
    except FileNotFoundError:
        logger.error("ffmpeg not found. Please install ffmpeg.")
        return False
