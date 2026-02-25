import logging
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from audio_manager.handlers.media import (
    download_media,
    enrich_with_steinsaltz,
    get_calendar,
    print_media_links,
    publish_uploads_to_sqs,
    upload_media_to_s3,
)
from audio_manager.infrastructure import DatabaseMediaSource, DependenciesContainer
from audio_manager.models.schemas import MediaEntry

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure logging with UTF-8 support for Windows console."""
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    setup_logging()
    container = DependenciesContainer()

    # Get media from configured source (see dependency_injection.py to switch)
    media_source = container.media_source()
    day_offsets: list[tuple[str, int]] = [
        ("yesterday", 1),
        ("today", 0),
        ("tomorrow", -1),
    ]

    # Reuse clients across all day runs
    gitlab_client = (
        container.gitlab_client()
        if isinstance(media_source, DatabaseMediaSource)
        else None
    )
    s3_uploader = container.s3_uploader()
    sqs_publisher = container.sqs_publisher()
    s3_client = container.s3_client()

    for day_label, days_ago in day_offsets:
        target_date = date.today() - timedelta(days=days_ago)
        logger.info("")
        logger.info("=" * 50)
        logger.info(
            "Processing %s (%s, days_ago=%d)",
            day_label,
            target_date.isoformat(),
            days_ago,
        )
        logger.info("=" * 50)

        media_links: list[MediaEntry] = media_source.get_media_entries(
            days_ago=days_ago
        )

        # Enrich with Steinsaltz commentary from GitLab (only for database mode)
        if isinstance(media_source, DatabaseMediaSource):
            calendar_entries = get_calendar(days_ago=days_ago)
            enrich_with_steinsaltz(media_links, calendar_entries, gitlab_client)

        print_media_links(media_links)

        with tempfile.TemporaryDirectory(
            prefix="transcription_",
            delete=True,
            ignore_cleanup_errors=True,
        ) as temp_dir:
            download_dir = Path(temp_dir)

            # Download media
            download_media(media_links, download_dir)
            logger.info("")
            logger.info("=" * 50)
            logger.info("Downloads complete. Files saved to: %s", download_dir)

            # Upload to S3
            uploaded = upload_media_to_s3(media_links, s3_uploader)
            logger.info("Uploaded %d files to S3", uploaded)

            # Publish to SQS (skips files already processed in FINAL_BUCKET)
            published = publish_uploads_to_sqs(media_links, sqs_publisher, s3_client)
            logger.info("Published %d messages to SQS", published)


if __name__ == "__main__":
    main()
