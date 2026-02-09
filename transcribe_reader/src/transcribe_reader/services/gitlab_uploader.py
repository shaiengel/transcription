"""GitLab uploader service for VTT files."""

import logging
import os
from datetime import date

from dotenv import load_dotenv

from transcribe_reader.infrastructure.gitlab_client import GitLabClient
from transcribe_reader.models.schemas import VttFile

logger = logging.getLogger(__name__)

GITLAB_TARGET_PATH = "backend/data/portal_transcriptions"


class GitLabUploader:
    """Uploads VTT files to GitLab repository."""

    def __init__(self, gitlab_client: GitLabClient):
        self._gitlab_client = gitlab_client
        load_dotenv()
        self._branch = os.getenv("GITLAB_BRANCH", "main")

    def upload(self, vtt_file: VttFile) -> bool:
        """Upload a single VTT file to GitLab."""
        if not vtt_file.content:
            logger.warning("No content for %s, skipping upload", vtt_file.s3_key)
            return False

        gitlab_path = f"{GITLAB_TARGET_PATH}/{vtt_file.s3_key}"
        vtt_file.gitlab_path = gitlab_path

        commit_message = f"Add transcription {vtt_file.s3_key}"

        return self._gitlab_client.upload_file(
            path=gitlab_path,
            content=vtt_file.content,
            commit_message=commit_message,
            branch=self._branch,
        )

    def batch_upload(self, vtt_files: list[VttFile]) -> int:
        """Upload multiple VTT files in a single commit.

        Returns count of files uploaded.
        """
        # Filter files with content
        files_to_upload = [f for f in vtt_files if f.content]

        if not files_to_upload:
            logger.info("No files to upload")
            return 0

        # Build actions for batch commit
        actions = []
        descriptions = []
        for vtt_file in files_to_upload:
            gitlab_path = f"{GITLAB_TARGET_PATH}/{vtt_file.s3_key}"
            vtt_file.gitlab_path = gitlab_path

            # Check if file exists to determine action type
            action_type = "update" if self._gitlab_client.file_exists(gitlab_path, self._branch) else "create"

            actions.append({
                "action": action_type,
                "file_path": gitlab_path,
                "content": vtt_file.content,
            })
            descriptions.append(f"- {vtt_file.s3_key}")

        # Build commit message with file descriptions
        commit_message = f"Sync {len(actions)} transcription(s) - {date.today().isoformat()}\n\n"
        commit_message += "\n".join(descriptions)

        if self._gitlab_client.batch_commit(actions, commit_message, self._branch):
            return len(actions)

        # Batch failed, fall back to individual uploads
        logger.warning("Batch commit failed, falling back to individual uploads")
        uploaded = 0
        for vtt_file in files_to_upload:
            if self.upload(vtt_file):
                uploaded += 1
        return uploaded
