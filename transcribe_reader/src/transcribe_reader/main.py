"""Entry point for transcribe_reader CLI."""

import logging
import sys

from transcribe_reader.handlers.sync import sync_transcriptions
from transcribe_reader.infrastructure import DependenciesContainer

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure logging with UTF-8 support for Windows console."""
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    """Main entry point."""
    setup_logging()
    logger.info("Starting transcription sync")

    try:
        container = DependenciesContainer()

        s3_downloader = container.s3_downloader()
        gitlab_uploader = container.gitlab_uploader()

        result = sync_transcriptions(s3_downloader, gitlab_uploader)

        logger.info("=" * 50)
        logger.info("Sync complete:")
        logger.info("  Media entries found: %d", result["media_count"])
        logger.info("  VTT files in S3: %d", result["available"])
        logger.info("  Downloaded: %d", result["downloaded"])
        logger.info("  Uploaded to GitLab: %d", result["uploaded"])

    except Exception as e:
        logger.error("Sync failed: %s", e)
        if "403" in str(e) or "Forbidden" in str(e):
            logger.error(
                "This is likely an IAM permissions issue. "
                "Ensure your AWS profile has s3:GetObject and s3:HeadObject permissions "
                "on the portal-daf-yomi-transcription bucket."
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
