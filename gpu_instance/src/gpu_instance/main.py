"""Main entry point for the GPU transcription worker."""

import logging
import sys

import torch

from gpu_instance.infrastructure import DependenciesContainer
from gpu_instance.handlers import run_worker_loop
from gpu_instance.services import load_model


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
    """Entry point for the SQS-driven transcription worker."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("Starting GPU Transcription Worker")
    logger.info("=" * 60)

    # Log GPU info
    logger.info("PyTorch version: %s", torch.__version__)
    logger.info("CUDA available: %s", torch.cuda.is_available())
    if torch.cuda.is_available():
        logger.info("CUDA version: %s", torch.version.cuda)
        logger.info("cuDNN enabled: %s", torch.backends.cudnn.enabled)

    # Initialize DI container
    logger.info("Initializing dependency injection container...")
    container = DependenciesContainer()

    # Pre-load Whisper model
    logger.info("Pre-loading Whisper model...")
    load_model()
    logger.info("Model ready")

    # Resolve dependencies from container
    sqs_receiver = container.sqs_receiver()
    s3_downloader = container.s3_downloader()
    s3_uploader = container.s3_uploader()
    formatters = container.formatters()

    logger.info("Source bucket: %s", s3_downloader.source_bucket)
    logger.info("Destination bucket: %s", s3_uploader.dest_bucket)
    logger.info("SQS queue: %s", sqs_receiver.queue_url)
    logger.info("Formatters: %d loaded", len(formatters))

    logger.info("=" * 60)
    logger.info("Starting SQS worker loop...")
    logger.info("=" * 60)

    try:
        run_worker_loop(sqs_receiver, s3_downloader, s3_uploader, formatters)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
