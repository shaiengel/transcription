import logging
from collections import Counter
from datetime import date

from audio_manager.services.database import (
    get_connection,
    get_media_links,
    get_today_calendar_entries,
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


def get_today_media_links() -> list[dict]:
    """Fetch today's media links from the database."""
    with get_connection() as conn:
        calendar_entries = get_today_calendar_entries(conn)

        all_media: list[dict] = []
        for massechet_id, daf_id in calendar_entries:
            media_list = get_media_links(conn, massechet_id, daf_id)
            all_media.extend(media_list)

        return all_media


def print_media_links(media_list: list[dict]) -> None:
    """Print media links and summary statistics."""
    today = date.today().isoformat()

    if not media_list:
        logger.info("No media links found for %s", today)
        return

    total_count = 0
    total_duration = 0
    language_counts: Counter[str] = Counter()
    language_durations: Counter[str] = Counter()

    for media in media_list:
        logger.info("ID: %s", media["media_id"])
        logger.info("  Link: %s", media["media_link"])
        logger.info("  Maggid: %s", media["maggid_description"])
        logger.info(
            "  Massechet: %s, Daf: %s",
            media["massechet_name"],
            media["daf_name"],
        )
        logger.info(
            "  Language: %s, Duration: %s",
            media["language_he"],
            format_duration(media["media_duration"]),
        )
        logger.info("")

        total_count += 1
        language = media["language_he"] or "Unknown"
        language_counts[language] += 1

        duration = media["media_duration"] or 0
        total_duration += duration
        language_durations[language] += duration

    # Print summary
    logger.info("=" * 50)
    logger.info("Total: %d media links", total_count)
    logger.info("Total duration: %s", format_duration(total_duration))
    logger.info("")
    logger.info("By language:")
    for language, count in language_counts.most_common():
        duration = language_durations[language]
        logger.info("  %s: %d (%s)", language, count, format_duration(duration))
