"""Main sync orchestration handler."""

import json
import logging
import os

from dotenv import load_dotenv

from transcribe_reader.infrastructure.sqs_client import SQSClient
from transcribe_reader.models.schemas import TranscriptionFile
from transcribe_reader.services.s3_downloader import S3Downloader
from transcribe_reader.services.gitlab_uploader import GitLabUploader

logger = logging.getLogger(__name__)

load_dotenv()
SQS_QUEUE_URL = os.getenv(
    "SQS_QUEUE_URL",
    "https://sqs.us-east-1.amazonaws.com/707072965202/sqs-fix-transcribes",
)
FIXED_TEXT_BUCKET = os.getenv("S3_FIXED_TEXT_BUCKET", "portal-daf-yomi-fixed-text")


def poll_sqs_messages(sqs_client: SQSClient) -> list[dict]:
    """Poll all available messages from SQS.

    Keeps polling until no more messages are returned.

    Returns:
        List of raw SQS message dicts.
    """
    all_messages = []

    while True:
        messages = sqs_client.receive_messages(SQS_QUEUE_URL, max_messages=10)
        if not messages:
            break
        all_messages.extend(messages)

    logger.info("Total messages polled from SQS: %d", len(all_messages))
    return all_messages


def parse_sqs_messages(messages: list[dict]) -> list[tuple[list[TranscriptionFile], str]]:
    """Parse SQS messages into TranscriptionFile objects.

    Returns:
        List of ([TranscriptionFile, ...], receipt_handle) tuples.
    """
    results = []

    for msg in messages:
        receipt_handle = msg["ReceiptHandle"]

        try:
            body = json.loads(msg["Body"])
        except json.JSONDecodeError:
            logger.warning("Failed to parse SQS message body: %s", msg["Body"][:100])
            continue

        stem = body.get("stem")

        if not stem:
            logger.warning("No stem in SQS message: %s", body)
            continue

        files = [
            TranscriptionFile(
                s3_key=f"{stem}.vtt",
                filename=f"{stem}.vtt",
                stem=stem,
            ),
            TranscriptionFile(
                s3_key=f"{stem}.srt",
                filename=f"{stem}.srt",
                stem=stem,
            ),
            TranscriptionFile(
                s3_key=f"{stem}.txt",
                filename=f"{stem}.txt",
                stem=stem,
                source_bucket=FIXED_TEXT_BUCKET,
            ),
        ]

        results.append((files, receipt_handle))

    return results


def sync_transcriptions(
    s3_downloader: S3Downloader,
    gitlab_uploader: GitLabUploader,
    sqs_client: SQSClient,
) -> dict:
    """Main sync orchestration function.

    1. Poll SQS for transcription stems
    2. Download .vtt/.srt from transcription bucket and .txt from fixed-text bucket
    3. Upload to GitLab
    4. Delete processed messages from SQS

    Returns summary dict with counts.
    """
    # Step 1: Poll SQS
    messages = poll_sqs_messages(sqs_client)

    if not messages:
        logger.info("No messages in SQS queue")
        return {"messages": 0, "downloaded": 0, "uploaded": 0, "deleted": 0}

    # Step 2: Parse messages
    message_entries = parse_sqs_messages(messages)
    files_to_process = [file for files, _ in message_entries for file in files]
    logger.info(
        "Parsed %d files from %d messages",
        len(files_to_process),
        len(messages),
    )

    # Step 3: Download from S3
    downloaded = 0
    for file_item in files_to_process:
        if s3_downloader.download(file_item):
            downloaded += 1

    logger.info("Downloaded %d/%d files from S3", downloaded, len(files_to_process))

    # Step 4: Upload to GitLab
    files_with_content = [file_item for file_item in files_to_process if file_item.content]
    uploaded = 0
    if files_with_content:
        uploaded = gitlab_uploader.batch_upload(files_with_content)

    # Step 5: Delete successfully processed messages from SQS
    deleted = 0
    for files, receipt_handle in message_entries:
        if all(file_item.content for file_item in files):
            if sqs_client.delete_message(SQS_QUEUE_URL, receipt_handle):
                deleted += 1
                logger.info("Deleted SQS message for stem: %s", files[0].stem)
        else:
            logger.warning(
                "Skipping delete for stem %s; missing one or more files",
                files[0].stem,
            )

    return {
        "messages": len(messages),
        "downloaded": downloaded,
        "uploaded": uploaded,
        "deleted": deleted,
    }
