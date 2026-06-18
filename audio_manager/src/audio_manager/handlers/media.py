import logging
import os
from collections import Counter
from datetime import date
from importlib import resources
from pathlib import Path

from dotenv import load_dotenv

from audio_manager.models.schemas import CalendarEntry, CalendarWindow, MediaEntry
from audio_manager.models.daf_text_fetcher import DafTextFetcher
from audio_manager.services.database import (
    get_connection,
    get_chapters_for_daf,
    get_massechet_bounds,
    get_massechet_data_by_name,
    get_massechet_sefaria_name_raw,
    get_media_links,
    get_calendar_entries,
)
from audio_manager.infrastructure.s3_client import S3Client
from audio_manager.services.s3_uploader import S3Uploader
from audio_manager.services.sqs_publisher import SQSPublisher

env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path, override=True)
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATES: dict[str, str] = {}

_SYSTEM_PROMPT_FILENAMES = [
    "system_prompt.template.md",
    "system_prompt.template.reasoning.md",
]

_END_MASSECHET_TEXT: str | None = None


def _get_end_massechet_text() -> str:
    global _END_MASSECHET_TEXT
    if _END_MASSECHET_TEXT is None:
        path = Path(__file__).parent.parent / "end_massechet.txt"
        _END_MASSECHET_TEXT = path.read_text(encoding="utf-8")
    return _END_MASSECHET_TEXT


def _get_system_prompt_template(filename: str) -> str:
    if filename not in _SYSTEM_PROMPT_TEMPLATES:
        template_path = resources.files("audio_manager") / filename
        _SYSTEM_PROMPT_TEMPLATES[filename] = template_path.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT_TEMPLATES[filename]


def _render_system_prompt(details: str, steinsaltz: str, filename: str) -> str:
    template = _get_system_prompt_template(filename)
    return template.format(details, steinsaltz)


# Maggid IDs that require max_word_split setting
_MAX_WORD_SPLIT_MAGGID_IDS = {129, 20, 39, 43, 123, 163}
_MAX_WORD_SPLIT_VALUE = 3000


def apply_max_word_split(media_list: list[MediaEntry]) -> None:
    """Set max_word_split for media entries from specific maggidim."""
    for media in media_list:
        if media.maggid_id in _MAX_WORD_SPLIT_MAGGID_IDS:
            media.max_word_split = _MAX_WORD_SPLIT_VALUE


def get_allowed_languages() -> set[str]:
    """Get allowed languages from environment."""
    languages = os.getenv("ALLOWED_LANGUAGES", "hebrew")
    return {lang.strip() for lang in languages.split(",")}


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
        calendar_entries = get_calendar_entries(conn, days_ago=0)

        all_media: list[MediaEntry] = []
        for entry in calendar_entries:
            media_list = get_media_links(conn, entry.massechet_id, entry.daf_id)
            all_media.extend(media_list)

        return all_media


def get_calendar_window(days_ago: int = 0) -> CalendarWindow:
    """Fetch today's, yesterday's, and tomorrow's calendar entries in one connection."""
    with get_connection() as conn:
        return CalendarWindow(
            today=get_calendar_entries(conn, days_ago=days_ago),
            yesterday=get_calendar_entries(conn, days_ago=days_ago + 1),
            tomorrow=get_calendar_entries(conn, days_ago=days_ago - 1),
        )

def _extract_words(text: str, count: int, from_end: bool = False) -> str:
    """Extract words from text.

    Args:
        text: The source text.
        count: Number of words to extract.
        from_end: If True, take last N words; otherwise take first N.

    Returns:
        Extracted words joined with spaces.
    """
    words = text.split()
    if not words:
        return ""
    selected = words[-count:] if from_end else words[:count]
    return " ".join(selected)


def _get_adjacent_steinsaltz(
    adjacent_entries: list[CalendarEntry],
    today_massechet_id: int,
    steinsaltz_cache: dict[tuple[int, int], str | None],
) -> str | None:
    """Pick the best adjacent daf Steinsaltz text.

    Prefers the entry with the same massechet_id as today (normal case).
    Falls back to the first available entry (handles massechet transitions).
    """
    # Prefer same massechet
    for entry in adjacent_entries:
        key = (entry.massechet_id, entry.daf_id)
        text = steinsaltz_cache.get(key)
        if text and entry.massechet_id == today_massechet_id:
            return text
    # Fallback to any available
    for entry in adjacent_entries:
        key = (entry.massechet_id, entry.daf_id)
        text = steinsaltz_cache.get(key)
        if text:
            return text
    return None


def enrich_with_steinsaltz(
    media_list: list[MediaEntry],
    calendar: CalendarWindow,
    text_fetcher: DafTextFetcher | None,
) -> None:
    """Enrich media entries with Steinsaltz commentary.

    Fetches Steinsaltz for today's daf plus adjacent dafim (yesterday/tomorrow)
    to provide broader context for transcription correction.

    Args:
        media_list: List of media entries to enrich.
        calendar: Calendar entries for today, yesterday, and tomorrow.
        text_fetcher: DafTextFetcher instance, or None if not configured.
    """
    if not text_fetcher:
        logger.warning("No text fetcher configured.")
        return

    if not calendar.today:
        logger.warning("No calendar entries to fetch Steinsaltz for")
        return

    # Determine boundary conditions: skip adjacent days if we're at massechet edges
    use_yesterday = True
    use_tomorrow = True
    for entry in calendar.today:
        bounds = get_massechet_bounds(entry.massechet_id)
        if bounds:
            massechet_start, massechet_end = bounds
            if entry.daf_id == massechet_start:
                use_yesterday = False
            if entry.daf_id == massechet_end:
                use_tomorrow = False

    # Collect all unique (massechet_id, daf_id) keys to avoid duplicate fetches
    all_entries: list[CalendarEntry] = []
    seen_keys: set[tuple[int, int]] = set()
    adjacent_days = [
        *(calendar.yesterday if use_yesterday else []),
        *(calendar.tomorrow if use_tomorrow else []),
    ]
    for entry in [*calendar.today, *adjacent_days]:
        key = (entry.massechet_id, entry.daf_id)
        if key not in seen_keys:
            seen_keys.add(key)
            all_entries.append(entry)

    # Cache Steinsaltz commentary per (massechet_id, daf_id)
    steinsaltz_cache: dict[tuple[int, int], str | None] = {}

    for entry in all_entries:
        cache_key = (entry.massechet_id, entry.daf_id)

        # Get Sefaria name from massechet_data.json (original casing for URL/path use)
        sefaria_name = get_massechet_sefaria_name_raw(entry.massechet_id)
        if not sefaria_name:
            logger.warning(
                "No Sefaria name found for massechet_id %d", entry.massechet_id
            )
            steinsaltz_cache[cache_key] = None
            continue

        steinsaltz = text_fetcher.fetch_for_daf(sefaria_name, entry.daf_id)

        if steinsaltz:
            logger.info(
                "Fetched Steinsaltz for %s daf %d (%d chars)",
                sefaria_name,
                entry.daf_id,
                len(steinsaltz),
            )
        else:
            logger.warning(
                "No Steinsaltz found for %s daf %d",
                sefaria_name,
                entry.daf_id,
            )

        steinsaltz_cache[cache_key] = steinsaltz

    adjacent_word_count = int(os.getenv("adjacent_word_count", "120"))

    # Update media entries with Steinsaltz commentary
    for media in media_list:
        # Set basic details
        media.details = f"Talmud Massechet: {media.massechet_name}, Daf: {media.daf_name}"

        # Find the calendar entry this media belongs to and set steinsaltz
        for entry in calendar.today:
            cache_key = (entry.massechet_id, entry.daf_id)
            today_text = steinsaltz_cache.get(cache_key)

            if not today_text:
                break

            # Build combined text with adjacent daf excerpts
            sections: list[str] = []

            if use_yesterday:
                yesterday_text = _get_adjacent_steinsaltz(
                    calendar.yesterday, entry.massechet_id, steinsaltz_cache
                )
                if yesterday_text:
                    excerpt = _extract_words(yesterday_text, adjacent_word_count, from_end=True)
                    sections.append(excerpt)

            sections.append(today_text)

            if use_tomorrow:
                tomorrow_text = _get_adjacent_steinsaltz(
                    calendar.tomorrow, entry.massechet_id, steinsaltz_cache
                )
                if tomorrow_text:
                    excerpt = _extract_words(tomorrow_text, adjacent_word_count, from_end=False)
                    sections.append(excerpt)

            media.steinsaltz = "\n\n".join(sections)
            break  # Only need to match one calendar entry


_GEMATRIA: dict[str, int] = {
    'א': 1, 'ב': 2, 'ג': 3, 'ד': 4, 'ה': 5, 'ו': 6, 'ז': 7, 'ח': 8, 'ט': 9,
    'י': 10, 'כ': 20, 'ך': 20, 'ל': 30, 'מ': 40, 'ם': 40, 'נ': 50, 'ן': 50,
    'ס': 60, 'ע': 70, 'פ': 80, 'ף': 80, 'צ': 90, 'ץ': 90,
    'ק': 100, 'ר': 200, 'ש': 300, 'ת': 400,
}


def hebrew_to_int(s: str) -> int:
    return sum(_GEMATRIA.get(c, 0) for c in s)


def enrich_with_steinsaltz_by_daf(
    media_list: list[MediaEntry],
    text_fetcher: DafTextFetcher | None,
) -> None:
    """Enrich media entries with Steinsaltz commentary using massechet+daf from the entry itself.

    Unlike enrich_with_steinsaltz, this does not use a calendar. The massechet and daf
    are taken from media.massechet_name and media.daf_name (a Hebrew gematria numeral).
    Adjacent dafs (daf±1) are included unless at massechet boundaries.
    """
    if not text_fetcher:
        logger.warning("No text fetcher configured.")
        return

    adjacent_word_count = int(os.getenv("adjacent_word_count", "120"))

    # Cache: (masechet_name, daf_id) → steinsaltz text
    steinsaltz_cache: dict[tuple[str, int], str | None] = {}
    # Cache: masechet_name → massechet data dict
    massechet_info_cache: dict[str, dict] = {}

    def _fetch(masechet_name: str, daf_id: int, sefaria_name: str) -> str | None:
        key = (masechet_name, daf_id)
        if key not in steinsaltz_cache:
            text = text_fetcher.fetch_for_daf(sefaria_name, daf_id)
            if text:
                logger.info("Fetched Steinsaltz for %s daf %d (%d chars)", sefaria_name, daf_id, len(text))
            else:
                logger.warning("No Steinsaltz found for %s daf %d", sefaria_name, daf_id)
            steinsaltz_cache[key] = text
        return steinsaltz_cache[key]

    for media in media_list:
        if not media.massechet_name or not media.daf_name:
            logger.warning("Missing massechet_name or daf_name on media %s", media.media_id)
            continue

        daf_id = hebrew_to_int(media.daf_name)

        if media.massechet_name not in massechet_info_cache:
            info = get_massechet_data_by_name(media.massechet_name)
            if not info:
                logger.warning("No massechet data found for '%s'", media.massechet_name)
                continue
            massechet_info_cache[media.massechet_name] = info
        info = massechet_info_cache[media.massechet_name]

        sefaria_name: str = info.get("massechet_english", "")
        massechet_start: int = info.get("massechet_start", daf_id)
        massechet_end: int = info.get("massechet_end", daf_id)

        use_prev = daf_id > massechet_start
        use_next = daf_id < massechet_end

        today_text = _fetch(media.massechet_name, daf_id, sefaria_name)

        massechet_id: int = info.get("massechet_id")
        chapters = get_chapters_for_daf(massechet_id, daf_id)
        chapter_parts = [
            f"פרק {ch['chapter_name']} שהוא פרק {ch['chapter_count']}"
            for ch in chapters
        ]
        chapter_str = "\n" + " and also ".join(chapter_parts) if chapter_parts else ""
        media.details = (
            f"a Talmud Massechet:{media.massechet_name} of Daf: {media.daf_name}"
            + chapter_str
        )

        if not today_text:
            continue

        sections: list[str] = []

        if use_prev:
            prev_text = _fetch(media.massechet_name, daf_id - 1, sefaria_name)
            if prev_text:
                sections.append(_extract_words(prev_text, adjacent_word_count, from_end=True))

        sections.append(today_text)

        if use_next:
            next_text = _fetch(media.massechet_name, daf_id + 1, sefaria_name)
            if next_text:
                sections.append(_extract_words(next_text, adjacent_word_count, from_end=False))

        steinsaltz = "\n\n".join(sections)
        if not use_next:
            end_text = _get_end_massechet_text().format(media.massechet_name, media.massechet_name, media.massechet_name)
            steinsaltz += "\n" + end_text
        media.steinsaltz = steinsaltz


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
        logger.info("  Details: %s", media.details)
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



def upload_media_to_s3(
    media_list: list[MediaEntry],
    s3_uploader: S3Uploader,
) -> int:
    """Upload downloaded media files and system prompt templates to S3.

    Returns count of uploaded files.
    """
    allowed_languages = get_allowed_languages()
    uploaded = 0
    for media in media_list:
        if media.language not in allowed_languages:
            continue
        if media.downloaded_path and media.downloaded_path.exists():
            key = f"{media.media_id}{media.downloaded_path.suffix}"
            if s3_uploader.upload_file(media.downloaded_path, media.audio_bucket or "", key):
                uploaded += 1

                if media.details and media.steinsaltz:
                    for filename in _SYSTEM_PROMPT_FILENAMES:
                        template_content = _render_system_prompt(media.details, media.steinsaltz, filename)
                        stem = filename.replace("system_prompt.", "").replace(".md", "")
                        template_key = f"{media.media_id}.{stem}.txt"
                        s3_uploader.upload_content(template_content, media.context_files_bucket or "", template_key)

    return uploaded


def publish_uploads_to_sqs(
    media_list: list[MediaEntry],
    sqs_publisher: SQSPublisher,
    s3_client: S3Client,
) -> int:
    """Publish uploaded media to SQS. Returns count of published messages.

    Skips files that have already been processed (VTT exists in subtitles_bucket).
    """
    allowed_languages = get_allowed_languages()
    published = 0
    skipped = 0

    for media in media_list:
        if media.language not in allowed_languages:
            continue
        if media.downloaded_path and media.downloaded_path.exists():
            stem = str(media.media_id)

            if media.subtitles_bucket and s3_client.file_exists(media.subtitles_bucket, f"{stem}.vtt"):
                logger.info("Skipping - already processed: %s.vtt", stem)
                skipped += 1
                continue

            if sqs_publisher.publish_upload(media.media_id):
                published += 1

    if skipped > 0:
        logger.info("Skipped %d already-processed files", skipped)
    return published
