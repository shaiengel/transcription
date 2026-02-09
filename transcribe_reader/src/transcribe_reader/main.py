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
        sqs_client = container.sqs_client()

        result = sync_transcriptions(s3_downloader, gitlab_uploader, sqs_client)

        logger.info("=" * 50)
        logger.info("Sync complete:")
        logger.info("  SQS messages: %d", result["messages"])
        logger.info("  Downloaded: %d", result["downloaded"])
        logger.info("  Uploaded to GitLab: %d", result["uploaded"])
        logger.info("  Deleted from SQS: %d", result["deleted"])

    except Exception as e:
        logger.error("Sync failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
