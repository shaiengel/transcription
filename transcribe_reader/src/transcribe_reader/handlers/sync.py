"""Main sync orchestration handler."""

import logging

from transcribe_reader.models.schemas import MediaInfo, VttFile
from transcribe_reader.services.database import (
    get_connection,
    get_media_ids,
    get_today_calendar_entries,
)
from transcribe_reader.services.s3_downloader import S3Downloader
from transcribe_reader.services.gitlab_uploader import GitLabUploader

logger = logging.getLogger(__name__)


def get_today_media_ids() -> list[MediaInfo]:
    """Fetch all media_ids for today's calendar entries."""
    with get_connection() as conn:
        calendar_entries = get_today_calendar_entries(conn)
        logger.info("Found %d calendar entries for today", len(calendar_entries))

        all_media: list[MediaInfo] = []
        for entry in calendar_entries:
            media_list = get_media_ids(conn, entry.massechet_id, entry.daf_id)
            all_media.extend(media_list)
            logger.info(
                "  Massechet %d, Daf %d: %d media entries",
                entry.massechet_id,
                entry.daf_id,
                len(media_list),
            )

        return all_media


def build_vtt_files(media_list: list[MediaInfo]) -> list[VttFile]:
    """Build VttFile objects from media info."""
    return [
        VttFile(
            media_id=media.media_id,
            s3_key=f"{media.media_id}.vtt",
            maggid_description=media.maggid_description,
            massechet_name=media.massechet_name,
            daf_name=media.daf_name,
            language=media.language,
        )
        for media in media_list
    ]


def check_s3_availability(
    vtt_files: list[VttFile],
    s3_downloader: S3Downloader,
) -> list[VttFile]:
    """Check which VTT files exist in S3.

    Returns list of files that exist.
    """
    available = []
    for vtt_file in vtt_files:
        if s3_downloader.check_exists(vtt_file):
            available.append(vtt_file)
            logger.info("Found in S3: %s", vtt_file.s3_key)
        else:
            logger.warning("Not found in S3: %s", vtt_file.s3_key)

    logger.info(
        "S3 check complete: %d/%d files available",
        len(available),
        len(vtt_files),
    )
    return available


def download_vtt_files(
    vtt_files: list[VttFile],
    s3_downloader: S3Downloader,
) -> int:
    """Download VTT file contents from S3.

    Returns count of successfully downloaded files.
    """
    downloaded = 0
    for vtt_file in vtt_files:
        if s3_downloader.download(vtt_file):
            downloaded += 1

    logger.info("Downloaded %d/%d files", downloaded, len(vtt_files))
    return downloaded


def upload_to_gitlab(
    vtt_files: list[VttFile],
    gitlab_uploader: GitLabUploader,
    batch: bool = True,
) -> int:
    """Upload VTT files to GitLab.

    Args:
        vtt_files: List of VTT files with content
        gitlab_uploader: GitLab uploader service
        batch: If True, upload all files in a single commit

    Returns count of uploaded files.
    """
    files_with_content = [f for f in vtt_files if f.content]

    if not files_with_content:
        logger.info("No files with content to upload")
        return 0

    if batch:
        return gitlab_uploader.batch_upload(files_with_content)
    else:
        uploaded = 0
        for vtt_file in files_with_content:
            if gitlab_uploader.upload(vtt_file):
                uploaded += 1
        return uploaded


def sync_transcriptions(
    s3_downloader: S3Downloader,
    gitlab_uploader: GitLabUploader,
) -> dict:
    """Main sync orchestration function.

    Returns summary dict with counts.
    """
    # Step 1: Get today's media IDs from database
    media_list = get_today_media_ids()

    if not media_list:
        logger.info("No media entries found for today")
        return {"media_count": 0, "available": 0, "downloaded": 0, "uploaded": 0}

    # Step 2: Build VTT file references
    vtt_files = build_vtt_files(media_list)
    logger.info("Looking for %d VTT files", len(vtt_files))

    # Step 3: Check S3 availability
    available_files = check_s3_availability(vtt_files, s3_downloader)

    if not available_files:
        logger.info("No VTT files found in S3")
        return {
            "media_count": len(media_list),
            "available": 0,
            "downloaded": 0,
            "uploaded": 0,
        }

    # Step 4: Download content
    downloaded = download_vtt_files(available_files, s3_downloader)

    # Step 5: Upload to GitLab
    uploaded = upload_to_gitlab(available_files, gitlab_uploader, batch=True)

    return {
        "media_count": len(media_list),
        "available": len(available_files),
        "downloaded": downloaded,
        "uploaded": uploaded,
    }
