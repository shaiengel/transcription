import logging
import sys

from audio_manager.handlers.media import get_today_media_links, print_media_links


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
    media_links = get_today_media_links()
    print_media_links(media_links)


if __name__ == "__main__":
    main()
