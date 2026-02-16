import logging
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def download_file(url: str, dest_path: Path) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }

    try:
        with httpx.Client(
            timeout=300,
            follow_redirects=True,
            headers=headers            
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)

        return True

    except Exception as e:
        logger.error("Failed to download %s: %s", url, e)
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
