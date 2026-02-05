"""GitLab client wrapper for python-gitlab library."""

import logging
from typing import Any

import gitlab
from gitlab.exceptions import GitlabGetError

logger = logging.getLogger(__name__)


class GitLabClient:
    """Handles GitLab API operations."""

    def __init__(self, url: str, private_token: str, project_id: str):
        self._gl = gitlab.Gitlab(url, private_token=private_token)
        self._project = self._gl.projects.get(project_id)
        logger.info("Connected to GitLab project: %s", self._project.path_with_namespace)

    def file_exists(self, path: str, branch: str = "main") -> bool:
        """Check if a file exists in the repository."""
        try:
            self._project.files.get(file_path=path, ref=branch)
            return True
        except GitlabGetError:
            return False

    def upload_file(
        self,
        path: str,
        content: str,
        commit_message: str,
        branch: str = "main",
    ) -> bool:
        """Upload or update a file in GitLab repository."""
        try:
            if self.file_exists(path, branch):
                # Update existing file
                f = self._project.files.get(file_path=path, ref=branch)
                f.content = content
                f.save(branch=branch, commit_message=commit_message)
                logger.info("Updated: %s", path)
            else:
                # Create new file
                self._project.files.create({
                    "file_path": path,
                    "branch": branch,
                    "content": content,
                    "commit_message": commit_message,
                })
                logger.info("Created: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to upload %s: %s", path, e)
            return False

    def batch_commit(
        self,
        actions: list[dict[str, Any]],
        commit_message: str,
        branch: str = "main",
    ) -> bool:
        """Perform batch commit with multiple file actions.

        Actions should be list of dicts with keys:
        - action: "create" | "update" | "delete"
        - file_path: str
        - content: str (for create/update)
        """
        try:
            self._project.commits.create({
                "branch": branch,
                "commit_message": commit_message,
                "actions": actions,
            })
            logger.info("Batch commit successful: %d files", len(actions))
            return True
        except Exception as e:
            logger.error("Batch commit failed: %s", e)
            return False
