import logging
import os
import sys
import tempfile
from pathlib import Path

from audio_manager.handlers.media import (
    get_allowed_languages,
    publish_uploads_to_sqs,
    upload_media_to_s3,
)
from audio_manager.infrastructure import DependenciesContainer
from audio_manager.services.media_registry import MediaRegistry

logger = logging.getLogger(__name__)


def setup_logging() -> None:
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
    fetcher = container.media_fetcher()
    s3_uploader = container.s3_uploader()
    sqs_publisher = container.sqs_publisher()
    s3_client = container.s3_client()
    media_registry: MediaRegistry = container.media_registry()
    allowed_languages = get_allowed_languages()

    media_list = fetcher.get_all_medias()

    for media in media_list:
        if media.language not in allowed_languages:
            continue

        logger.info("")
        logger.info("=" * 50)
        logger.info("Processing media_id=%s", media.media_id)

        suffix = ".mp3" if media.file_type == "mp4" else f".{media.file_type or 'mp3'}"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.close()  # release handle so download_one can write to the path on Windows
            try:
                if not fetcher.download_media(media, tmp_path):
                    logger.warning("Download failed for media_id=%s", media.media_id)
                    continue

                media.downloaded_path = tmp_path
                media.audio_bucket = os.getenv("AUDIO_BUCKET", "")
                media.context_files_bucket = os.getenv("CONTEXT_FILES_BUCKET", "")
                media.transcription_bucket = os.getenv("TRANSCRIPTION_BUCKET", "")
                media.fixed_transcription_bucket = os.getenv("FIXED_TRANSCRIPTION_BUCKET", "")
                media.subtitles_bucket = os.getenv("SUBTITLES_BUCKET", "")
                upload_media_to_s3([media], s3_uploader)
                media_registry.set_db_entry([media])
                publish_uploads_to_sqs([media], sqs_publisher, s3_client)
            finally:
                tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
