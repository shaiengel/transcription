"""Main entry point for the GPU timestamp alignment worker."""

import logging
import sys

from gpu_timestamp.config import config
from gpu_timestamp.infrastructure import DependenciesContainer
from gpu_timestamp.handlers import run_worker_loop
from gpu_timestamp.services.aligner import load_model


def setup_logging() -> None:
    """Configure logging with UTF-8 support."""
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    """Entry point for the SQS-driven timestamp alignment worker."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("Starting GPU Timestamp Alignment Worker")
    logger.info("=" * 60)

    # Validate configuration
    config.validate()

    # Initialize DI container
    logger.info("Initializing dependency injection container...")
    container = DependenciesContainer()

    # Pre-load stable-whisper model
    logger.info("Pre-loading stable-whisper model: %s", config.model_name)
    load_model(config.model_name, config.device, config.whisper_cache)
    logger.info("Model ready")

    # Resolve dependencies from container
    sqs_receiver = container.sqs_receiver()
    s3_downloader = container.s3_downloader()
    s3_uploader = container.s3_uploader()
    sqs_sender = container.sqs_sender()

    logger.info("Audio bucket: %s", s3_downloader.audio_bucket)
    logger.info("Text bucket: %s", s3_downloader.text_bucket)
    logger.info("Output bucket: %s", s3_uploader.output_bucket)
    logger.info("SQS input queue: %s", sqs_receiver.queue_url)
    logger.info("SQS final queue: %s", sqs_sender.queue_url)
    logger.info("Language: %s", config.language)

    logger.info("=" * 60)
    logger.info("Starting SQS worker loop...")
    logger.info("=" * 60)

    try:
        run_worker_loop(sqs_receiver, s3_downloader, s3_uploader, sqs_sender)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
