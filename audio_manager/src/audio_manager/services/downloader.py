import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def download_file(url: str, dest_path: Path) -> bool:
    # Extract domain for Referer header (helps bypass hotlink protection)
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": referer,
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
