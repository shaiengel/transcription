import logging
import sys
import tempfile
from pathlib import Path

from audio_manager.handlers.media import (
    download_today_media,
    enrich_with_steinsaltz,
    get_today_calendar,
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
    media_links: list[MediaEntry] = media_source.get_media_entries()

    # Enrich with Steinsaltz commentary from GitLab (only for database mode)
    if isinstance(media_source, DatabaseMediaSource):
        gitlab_client = container.gitlab_client()
        calendar_entries = get_today_calendar()
        enrich_with_steinsaltz(media_links, calendar_entries, gitlab_client)

    print_media_links(media_links)

    with tempfile.TemporaryDirectory(
        prefix="transcription_",
        delete=True,
        ignore_cleanup_errors=True,
    ) as temp_dir:
        download_dir = Path(temp_dir)

        # Download media
        download_today_media(media_links, download_dir)
        logger.info("")
        logger.info("=" * 50)
        logger.info("Downloads complete. Files saved to: %s", download_dir)

        # Upload to S3
        s3_uploader = container.s3_uploader()
        uploaded = upload_media_to_s3(media_links, s3_uploader)
        logger.info("Uploaded %d files to S3", uploaded)

        # Publish to SQS (skips files already processed in FINAL_BUCKET)
        sqs_publisher = container.sqs_publisher()
        s3_client = container.s3_client()
        published = publish_uploads_to_sqs(media_links, sqs_publisher, s3_client)
        logger.info("Published %d messages to SQS", published)


if __name__ == "__main__":
    main()
