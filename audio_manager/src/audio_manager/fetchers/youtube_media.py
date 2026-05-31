import json
import logging
from pathlib import Path

import yt_dlp

from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


class YouTubeMedia(MediaFetcher):
    """Downloads YouTube audio for the shared pipeline.

    JSON format:
    [
      {
        "url": "https://www.youtube.com/watch?v=...",
        "media_id": 12345,
        "language": "hebrew",
        "details": "Shiur description"
      }
    ]
    """

    def __init__(self, json_path: Path) -> None:
        self._json_path = json_path

    def get_all_medias(self) -> list[MediaEntry]:
        with open(self._json_path, encoding="utf-8") as f:
            entries = json.load(f)

        media_list: list[MediaEntry] = []
        for entry in entries:
            media_list.append(
                MediaEntry(
                    media_id=entry["media_id"],
                    media_link=entry["url"],
                    language=entry.get("language", "hebrew"),
                    details=entry.get("details", ""),
                    file_type="mp3",
                    source="youtube",
                )
            )
        return media_list

    def download_media(self, media: MediaEntry, path: Path) -> bool:
        return self._download(media.media_link, path)

    def _download(self, url: str, dest: Path) -> bool:
        """Download audio from YouTube URL and write mp3 to dest."""
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(dest.with_suffix("")),  # yt-dlp appends extension
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "quiet": True,
            "no_warnings": False,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            mp3_output = dest.with_suffix("").with_suffix(".mp3")
            if mp3_output != dest and mp3_output.exists():
                mp3_output.replace(dest)
            return dest.exists() and dest.stat().st_size > 0
        except Exception as e:
            logger.error("yt-dlp error for %s: %s", url, e)
            return False
