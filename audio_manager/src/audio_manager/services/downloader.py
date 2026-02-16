import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def download_file(url: str, dest_path: Path) -> bool:
    """Download file using curl (bypasses TLS fingerprinting issues)."""
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    try:
        result = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time", "300",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-H", f"Referer: {referer}",
                "-o", str(dest_path),
                url,
            ],
            check=True,
            capture_output=True,
        )
        return True

    except subprocess.CalledProcessError as e:
        logger.error("Failed to download %s: %s", url, e.stderr.decode())
        return False
    except FileNotFoundError:
        logger.error("curl not found. Please install curl.")
        return False


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
