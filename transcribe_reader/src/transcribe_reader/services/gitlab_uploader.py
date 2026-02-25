"""GitLab uploader service for transcription files."""

import logging
import os
from datetime import date

from dotenv import load_dotenv

from transcribe_reader.infrastructure.gitlab_client import GitLabClient
from transcribe_reader.models.schemas import TranscriptionFile

logger = logging.getLogger(__name__)

GITLAB_TARGET_PATH = "backend/data/portal_transcriptions"


class GitLabUploader:
    """Uploads transcription files to GitLab repository."""

    def __init__(self, gitlab_client: GitLabClient):
        self._gitlab_client = gitlab_client
        load_dotenv()
        self._branch = os.getenv("GITLAB_BRANCH", "main")

    def upload(self, transcription_file: TranscriptionFile) -> bool:
        """Upload a single transcription file to GitLab."""
        if not transcription_file.content:
            logger.warning("No content for %s, skipping upload", transcription_file.s3_key)
            return False

        gitlab_path = f"{GITLAB_TARGET_PATH}/{transcription_file.s3_key}"
        transcription_file.gitlab_path = gitlab_path

        #commit_message = f"Add transcription {transcription_file.s3_key}"
        commit_message = f"Add transcription {transcription_file.s3_key}"

        return self._gitlab_client.upload_file(
            path=gitlab_path,
            content=transcription_file.content,
            commit_message=commit_message,
            branch=self._branch,
        )

    def batch_upload(self, transcription_files: list[TranscriptionFile]) -> int:
        """Upload multiple transcription files in a single commit.

        Returns count of files uploaded.
        """
        # Filter files with content
        files_to_upload = [f for f in transcription_files if f.content]

        if not files_to_upload:
            logger.info("No files to upload")
            return 0

        # Build actions for batch commit
        actions = []
        descriptions = []
        for transcription_file in files_to_upload:
            gitlab_path = f"{GITLAB_TARGET_PATH}/{transcription_file.s3_key}"
            transcription_file.gitlab_path = gitlab_path

            # Check if file exists to determine action type
            action_type = "update" if self._gitlab_client.file_exists(gitlab_path, self._branch) else "create"

            actions.append({
                "action": action_type,
                "file_path": gitlab_path,
                "content": transcription_file.content,
            })
            descriptions.append(f"- {transcription_file.s3_key}")

        # Build commit message with file descriptions
        commit_message = f"Sync {len(actions)} transcription(s) - {date.today().isoformat()}\n\n"
        commit_message += "\n".join(descriptions)

        if self._gitlab_client.batch_commit(actions, commit_message, self._branch):
            return len(actions)

        # Batch failed, fall back to individual uploads
        logger.warning("Batch commit failed, falling back to individual uploads")
        uploaded = 0
        for transcription_file in files_to_upload:
            if self.upload(transcription_file):
                uploaded += 1
        return uploaded
