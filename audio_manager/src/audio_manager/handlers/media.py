import logging
import os
from collections import Counter
from datetime import date
from importlib import resources
from pathlib import Path

from dotenv import load_dotenv

from audio_manager.models.schemas import CalendarEntry, MediaEntry
from audio_manager.infrastructure.gitlab_client import GitLabClient
from audio_manager.services.database import (
    get_connection,
    get_massechet_sefaria_name,
    get_media_links,
    get_today_calendar_entries,
)
from audio_manager.services.sefaria_fetcher import fetch_steinsaltz_for_daf
from audio_manager.services.downloader import (
    download_file,
    extract_audio_from_mp4,
)
from audio_manager.infrastructure.s3_client import S3Client
from audio_manager.services.s3_uploader import S3Uploader
from audio_manager.services.sqs_publisher import SQSPublisher

logger = logging.getLogger(__name__)

# Load system prompt template once at module level
_SYSTEM_PROMPT_TEMPLATE: str | None = None


def _get_system_prompt_template() -> str:
    """Load the system prompt template from package resources."""
    global _SYSTEM_PROMPT_TEMPLATE
    if _SYSTEM_PROMPT_TEMPLATE is None:
        template_path = resources.files("audio_manager") / "system_prompt.template.txt"
        _SYSTEM_PROMPT_TEMPLATE = template_path.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT_TEMPLATE


def _render_system_prompt(details: str, steinsaltz: str) -> str:
    """Render the system prompt template with details and Steinsaltz commentary."""
    template = _get_system_prompt_template()
    return template.format(details, steinsaltz)


def get_allowed_languages() -> set[str]:
    """Get allowed languages from environment."""
    load_dotenv()
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
        calendar_entries = get_today_calendar_entries(conn)

        all_media: list[MediaEntry] = []
        for entry in calendar_entries:
            media_list = get_media_links(conn, entry.massechet_id, entry.daf_id)
            all_media.extend(media_list)

        return all_media


def get_today_calendar() -> list[CalendarEntry]:
    """Fetch today's calendar entries from the database."""
    with get_connection() as conn:
        return get_today_calendar_entries(conn)


def enrich_with_steinsaltz(
    media_list: list[MediaEntry],
    calendar_entries: list[CalendarEntry],
    gitlab_client: GitLabClient | None,
) -> None:
    """Enrich media entries with Steinsaltz commentary from GitLab.

    Updates the 'details' field of each media entry with the Steinsaltz
    commentary fetched from GitLab Sefaria pages.

    Args:
        media_list: List of media entries to enrich.
        calendar_entries: Today's calendar entries with massechet_id and daf_id.
        gitlab_client: GitLab client instance, or None if not configured.
    """
    if not gitlab_client:
        logger.warning(
            "GitLab client not configured. "
            "Set GITLAB_PRIVATE_TOKEN and GITLAB_PROJECT_ID in .env"
        )
        return

    if not calendar_entries:
        logger.warning("No calendar entries to fetch Steinsaltz for")
        return

    # Cache Steinsaltz commentary per (massechet_id, daf_id)
    steinsaltz_cache: dict[tuple[int, int], str | None] = {}

    with get_connection() as conn:
        for entry in calendar_entries:
            cache_key = (entry.massechet_id, entry.daf_id)

            # Get Sefaria folder name directly from massechet_stein table
            sefaria_name = get_massechet_sefaria_name(conn, entry.massechet_id)
            if not sefaria_name:
                logger.warning(
                    "No Sefaria name found for massechet_id %d", entry.massechet_id
                )
                steinsaltz_cache[cache_key] = None
                continue

            # Fetch Steinsaltz commentary from GitLab
            branch = os.getenv("GITLAB_BRANCH", "main")
            steinsaltz = fetch_steinsaltz_for_daf(
                gitlab_client, sefaria_name, entry.daf_id, branch
            )

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

    # Update media entries with Steinsaltz commentary
    for media in media_list:
        # Set basic details
        media.details = f"Talmud Massechet: {media.massechet_name}, Daf: {media.daf_name}"

        # Find the calendar entry this media belongs to and set steinsaltz
        for entry in calendar_entries:
            cache_key = (entry.massechet_id, entry.daf_id)
            steinsaltz_text = steinsaltz_cache.get(cache_key)

            if steinsaltz_text:
                media.steinsaltz = steinsaltz_text
            break  # Only need to match one calendar entry


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


def download_today_media(media_list: list[MediaEntry], download_dir: Path) -> None:
    """Download ALL media files and set downloaded_path on each.

    Files that already have downloaded_path set (e.g., from LocalDiskMediaSource)
    are skipped.
    """
    # Count files that need downloading
    to_download = [m for m in media_list if m.downloaded_path is None]
    if not to_download:
        logger.info("All %d media files already have local paths", len(media_list))
        return

    logger.info("Downloading %d media files to %s", len(to_download), download_dir)

    for media in to_download:
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
        # break


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
            # Upload audio file
            key = media.downloaded_path.name
            if s3_uploader.upload_file(media.downloaded_path, key):
                uploaded += 1

            # Upload system prompt template with same stem
            if media.details and media.steinsaltz:
                template_content = _render_system_prompt(media.details, media.steinsaltz)
                template_key = media.downloaded_path.stem + ".template.txt"
                s3_uploader.upload_content(template_content, template_key)

    return uploaded


def publish_uploads_to_sqs(
    media_list: list[MediaEntry],
    sqs_publisher: SQSPublisher,
    s3_client: S3Client,
) -> int:
    """Publish uploaded media to SQS. Returns count of published messages.

    Skips files that have already been processed (VTT exists in FINAL_BUCKET).
    """
    load_dotenv()
    final_bucket = os.getenv("FINAL_BUCKET")
    allowed_languages = get_allowed_languages()
    published = 0
    skipped = 0

    for media in media_list:
        if media.language not in allowed_languages:
            continue
        if media.downloaded_path and media.downloaded_path.exists():
            stem = media.downloaded_path.stem

            # Skip if already processed (VTT exists in FINAL_BUCKET)
            if final_bucket and s3_client.file_exists(final_bucket, f"{stem}.vtt"):
                logger.info("Skipping - already processed: %s.vtt", stem)
                skipped += 1
                continue

            key = media.downloaded_path.name
            if sqs_publisher.publish_upload(key, media.language, media.details):
                published += 1

    if skipped > 0:
        logger.info("Skipped %d already-processed files", skipped)
    return published
