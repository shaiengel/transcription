"""Main entry point for the GPU transcription worker."""

import argparse
import logging
import sys
import tempfile

from gpu_instance.config import config
from gpu_instance.handlers.s3_handler import S3Handler
from gpu_instance.services import (
    load_model,
    transcribe,
    collect_segments,
    segments_to_vtt,
    segments_to_text,
    save_vtt,
    save_text,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def process_file(s3_key: str, s3_handler: S3Handler, temp_dir: str) -> bool:
    """
    Process a single audio file.

    Args:
        s3_key: S3 key of the audio file.
        s3_handler: S3 handler instance.
        temp_dir: Temporary directory for file operations.

    Returns:
        True if processing succeeded, False otherwise.
    """
    audio_path = None
    vtt_path = None

    try:
        logger.info(f"Processing: {s3_key}")

        # Download audio from S3
        audio_path = s3_handler.download_audio(s3_key)

        # Transcribe
        segments_iter, info = transcribe(audio_path)        

        # Collect all segments
        segments = collect_segments(segments_iter)

        # Convert to VTT
        vtt_content = segments_to_vtt(segments)

        # Convert to plain text
        text_content = segments_to_text(segments)

        # Save VTT locally
        vtt_path = save_vtt(vtt_content, audio_path, temp_dir)

        # Save text locally for RAG
        text_path = save_text(text_content, audio_path, temp_dir)

        # Upload to S3
        vtt_key = s3_handler.upload_file(vtt_path, s3_key)
        txt_key = s3_handler.upload_file(text_path, s3_key)

        logger.info(f"Successfully processed {s3_key} -> {vtt_key}, {txt_key}")
        return True

    except Exception as e:
        logger.error(f"Failed to process {s3_key}: {e}", exc_info=True)
        return False

    finally:
        # Cleanup local files
        if audio_path:
            s3_handler.cleanup_local_file(audio_path)
        # if vtt_path:
        #     s3_handler.cleanup_local_file(vtt_path)


def run_worker(files: list[str]) -> None:
    """
    Process audio files from S3.

    Args:
        files: List of S3 keys to process.
    """
    logger.info("=" * 60)
    logger.info("Starting GPU Transcription Worker")
    logger.info("=" * 60)

    # Validate config
    config.validate()

    if not files:
        logger.info("No files to process")
        return

    logger.info(f"Files to process: {len(files)}")

    # Pre-load model
    logger.info("Pre-loading Whisper model...")
    load_model()
    logger.info("Model ready")

    # Create temp directory once for all files
    with tempfile.TemporaryDirectory(
        prefix="transcription_",
        delete=False,
        ignore_cleanup_errors=True,
    ) as temp_dir:
        logger.info(f"Using temp directory: {temp_dir}")

        # Initialize S3 handler with temp directory
        s3_handler = S3Handler(temp_dir)

        # Process each file
        success_count = 0
        fail_count = 0

        for i, s3_key in enumerate(files, start=1):
            logger.info(f"[{i}/{len(files)}] Processing {s3_key}")

            if process_file(s3_key, s3_handler, temp_dir):
                success_count += 1
            else:
                fail_count += 1

        # Summary
        logger.info("=" * 60)
        logger.info(f"Completed: {success_count} success, {fail_count} failed")
        logger.info("=" * 60)


def main():
    """Entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Transcribe audio files from S3 to VTT format"
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="S3 keys of audio files to process (e.g., file1.mp3 file2.mp3)",
    )

    args = parser.parse_args()

    try:
        run_worker(files=args.files)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
