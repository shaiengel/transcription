import logging
from collections import Counter
from datetime import date
from pathlib import Path

from audio_manager.models.schemas import MediaEntry
from audio_manager.services.database import (
    get_connection,
    get_media_links,
    get_today_calendar_entries,
)
from audio_manager.services.downloader import (
    create_download_dir,
    download_file,
    extract_audio_from_mp4,
)

logger = logging.getLogger(__name__)


def format_duration(seconds: int | None) -> str:
    """Format duration in seconds to HH:MM:SS or MM:SS."""
    if seconds is None or seconds == 0:
        return "N/A"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def get_today_media_links() -> list[MediaEntry]:
    """Fetch today's media links from the database."""
    with get_connection() as conn:
        calendar_entries = get_today_calendar_entries(conn)

        all_media: list[MediaEntry] = []
        for entry in calendar_entries:
            media_list = get_media_links(conn, entry.massechet_id, entry.daf_id)
            all_media.extend(media_list)

        return all_media


def print_media_links(media_list: list[MediaEntry]) -> None:
    """Print media links and summary statistics."""
    today = date.today().isoformat()

    if not media_list:
        logger.info("No media links found for %s", today)
        return

    total_count = 0
    total_duration = 0
    language_counts: Counter[str] = Counter()
    language_durations: Counter[str] = Counter()
    file_type_counts: Counter[str] = Counter()
    file_type_durations: Counter[str] = Counter()

    for media in media_list:
        logger.info("ID: %s", media.media_id)
        logger.info("  Link: %s", media.media_link)
        logger.info("  File type: %s", media.file_type)
        logger.info("  Maggid: %s", media.maggid_description)
        logger.info(
            "  Massechet: %s, Daf: %s",
            media.massechet_name,
            media.daf_name,
        )
        logger.info(
            "  Language: %s, Duration: %s",
            media.language,
            format_duration(media.media_duration),
        )
        logger.info("")

        total_count += 1
        duration = media.media_duration or 0
        total_duration += duration

        language = media.language or "Unknown"
        language_counts[language] += 1
        language_durations[language] += duration

        file_type = media.file_type or "Unknown"
        file_type_counts[file_type] += 1
        file_type_durations[file_type] += duration

    # Print summary
    logger.info("=" * 50)
    logger.info("Total: %d media links", total_count)
    logger.info("Total duration: %s", format_duration(total_duration))
    logger.info("")
    logger.info("By file type:")
    for file_type, count in file_type_counts.most_common():
        duration = file_type_durations[file_type]
        logger.info("  %s: %d (%s)", file_type, count, format_duration(duration))
    logger.info("")
    logger.info("By language:")
    for language, count in language_counts.most_common():
        duration = language_durations[language]
        logger.info("  %s: %d (%s)", language, count, format_duration(duration))


def download_today_media(media_list: list[MediaEntry]) -> Path:
    """Download ALL media files and set downloaded_path on each."""
    download_dir = create_download_dir()

    logger.info("Downloading %d media files to %s", len(media_list), download_dir)

    for media in media_list:
        media_id = media.media_id
        file_type = media.file_type
        url = media.media_link

        if file_type == "mp4":
            # Download mp4, then extract to mp3
            mp4_path = download_dir / f"{media_id}.mp4"
            mp3_path = download_dir / f"{media_id}.mp3"

            logger.info("Downloading %s...", mp4_path.name)
            if download_file(url, mp4_path):
                logger.info("Extracting audio from %s...", mp4_path.name)
                if extract_audio_from_mp4(mp4_path, mp3_path):
                    mp4_path.unlink()  # Remove mp4 after extraction
                    media.downloaded_path = mp3_path
                    logger.info("Saved: %s", mp3_path.name)
                else:
                    logger.warning("Failed to extract audio, removing mp4")
                    mp4_path.unlink()  # Remove failed mp4
        else:
            # Download as-is (mp3)
            dest_path = download_dir / f"{media_id}.{file_type}"
            logger.info("Downloading %s...", dest_path.name)
            if download_file(url, dest_path):
                media.downloaded_path = dest_path
                logger.info("Saved: %s", dest_path.name)

    return download_dir
