"""Main sync orchestration handler."""

import json
import logging
import os

from dotenv import load_dotenv

from transcribe_reader.infrastructure.sqs_client import SQSClient
from transcribe_reader.models.schemas import VttFile
from transcribe_reader.services.s3_downloader import S3Downloader
from transcribe_reader.services.gitlab_uploader import GitLabUploader

logger = logging.getLogger(__name__)

load_dotenv()
SQS_QUEUE_URL = os.getenv(
    "SQS_QUEUE_URL",
    "https://sqs.us-east-1.amazonaws.com/707072965202/sqs-fix-transcribes",
)


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


def parse_sqs_messages(messages: list[dict]) -> list[tuple[VttFile, str]]:
    """Parse SQS messages into VttFile objects.

    Returns:
        List of (VttFile, receipt_handle) tuples.
    """
    results = []

    for msg in messages:
        receipt_handle = msg["ReceiptHandle"]

        try:
            body = json.loads(msg["Body"])
        except json.JSONDecodeError:
            logger.warning("Failed to parse SQS message body: %s", msg["Body"][:100])
            continue

        filename = body.get("filename", "")
        if not filename:
            logger.warning("No filename in SQS message: %s", body)
            continue

        stem = filename.rsplit(".", 1)[0]

        vtt_file = VttFile(
            s3_key=filename,
            filename=filename,
            stem=stem,
        )

        results.append((vtt_file, receipt_handle))

    return results


def sync_transcriptions(
    s3_downloader: S3Downloader,
    gitlab_uploader: GitLabUploader,
    sqs_client: SQSClient,
) -> dict:
    """Main sync orchestration function.

    1. Poll SQS for VTT filenames
    2. Download VTT content from S3 (final-transcription bucket)
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
    vtt_entries = parse_sqs_messages(messages)
    logger.info("Parsed %d VTT entries from %d messages", len(vtt_entries), len(messages))

    # Step 3: Download from S3
    downloaded = 0
    for vtt_file, _ in vtt_entries:
        if s3_downloader.download(vtt_file):
            downloaded += 1

    logger.info("Downloaded %d/%d files from S3", downloaded, len(vtt_entries))

    # Step 4: Upload to GitLab
    files_with_content = [vtt_file for vtt_file, _ in vtt_entries if vtt_file.content]
    uploaded = 0
    if files_with_content:
        uploaded = gitlab_uploader.batch_upload(files_with_content)

    # Step 5: Delete successfully processed messages from SQS
    deleted = 0
    for vtt_file, receipt_handle in vtt_entries:
        if vtt_file.content:
            if sqs_client.delete_message(SQS_QUEUE_URL, receipt_handle):
                deleted += 1
                logger.info("Deleted SQS message for: %s", vtt_file.s3_key)

    return {
        "messages": len(messages),
        "downloaded": downloaded,
        "uploaded": uploaded,
        "deleted": deleted,
    }
