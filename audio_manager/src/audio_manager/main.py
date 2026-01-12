import logging
import sys

from audio_manager.handlers.media import (
    download_today_media,
    get_today_media_links,
    print_media_links,
)
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
    media_links: list[MediaEntry] = get_today_media_links()
    print_media_links(media_links)

    # Download media
    download_dir = download_today_media(media_links)
    logger.info("")
    logger.info("=" * 50)
    logger.info("Downloads complete. Files saved to: %s", download_dir)


if __name__ == "__main__":
    main()
