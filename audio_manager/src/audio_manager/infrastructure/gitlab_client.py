"""GitLab client wrapper for fetching Sefaria pages."""

import logging

import gitlab
from gitlab.exceptions import GitlabGetError

logger = logging.getLogger(__name__)


class GitLabClient:
    """Handles GitLab API operations for reading files."""

    def __init__(self, url: str, private_token: str, project_id: str):
        self._gl = gitlab.Gitlab(url, private_token=private_token)
        self._project = self._gl.projects.get(project_id)
        logger.info("Connected to GitLab project: %s", self._project.path_with_namespace)

    def get_file_content(self, path: str, branch: str = "main") -> str | None:
        """Get the content of a file from the repository.

        Returns None if the file doesn't exist.
        """
        try:
            f = self._project.files.get(file_path=path, ref=branch)
            return f.decode().decode("utf-8")
        except GitlabGetError:
            logger.debug("File not found: %s", path)
            return None
        except Exception as e:
            logger.error("Failed to fetch %s: %s", path, e)
            return None

    def file_exists(self, path: str, branch: str = "main") -> bool:
        """Check if a file exists in the repository."""
        try:
            self._project.files.get(file_path=path, ref=branch)
            return True
        except GitlabGetError:
            return False
