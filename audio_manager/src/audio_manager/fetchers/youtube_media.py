import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from audio_manager.handlers.media import enrich_with_steinsaltz_by_daf
from audio_manager.models.daf_text_fetcher import DafTextFetcher
from audio_manager.models.media_fetcher import MediaFetcher
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


def _video_id(url: str) -> str:
    return parse_qs(urlparse(url).query)["v"][0]


class YouTubeMedia(MediaFetcher):
    """Downloads YouTube audio for the shared pipeline.

    JSON format:
    [
      {
        "url": "https://www.youtube.com/watch?v=...",
        "lecturer": "...",
        "title": "...",
        "language": "hebrew"
      }
    ]
    """

    def __init__(self, json_path: Path, text_fetcher: DafTextFetcher | None = None) -> None:
        self._json_path = json_path
        self._text_fetcher = text_fetcher

    def get_all_medias(self) -> list[MediaEntry]:
        with open(self._json_path, encoding="utf-8") as f:
            entries = json.load(f)

        media_list: list[MediaEntry] = []
        for entry in entries:
            url = entry["url"]
            lecturer = entry.get("lecturer", "")
            title = entry.get("title", "")
            details = f"{lecturer} & {title}" if lecturer and title else lecturer or title
            title_parts = title.split()
            massechet_name = title_parts[0] if title_parts else None
            daf_name_he = title_parts[1] if len(title_parts) > 1 else None
            media_list.append(
                MediaEntry(
                    media_id=_video_id(url),
                    media_link=url,
                    language=entry.get("language", "hebrew"),
                    details=details,
                    massechet_name=massechet_name,
                    daf_name=daf_name_he,
                    file_type="mp3",
                    source="youtube",
                )
            )
        enrich_with_steinsaltz_by_daf(media_list, self._text_fetcher)
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
