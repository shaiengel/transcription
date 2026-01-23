"""Dependency injection container."""

import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from transcribe_reader.infrastructure.s3_client import S3Client
from transcribe_reader.infrastructure.gitlab_client import GitLabClient


def _create_session() -> boto3.Session:
    """Create boto3 session using profile from environment."""
    load_dotenv()
    profile = os.getenv("AWS_PROFILE", "default")
    return boto3.Session(profile_name=profile)


def _create_gitlab_client() -> GitLabClient:
    """Factory for GitLabClient."""
    load_dotenv()
    url = os.getenv("GITLAB_URL", "https://gitlab.com")
    token = os.getenv("GITLAB_PRIVATE_TOKEN", "")
    project_id = os.getenv("GITLAB_PROJECT_ID", "")  # e.g., "llm241203/dy6"

    if not token:
        raise ValueError("GITLAB_PRIVATE_TOKEN environment variable is required")
    if not project_id:
        raise ValueError("GITLAB_PROJECT_ID environment variable is required")

    return GitLabClient(url, token, project_id)


def _create_s3_downloader(s3_client: S3Client):
    """Factory for S3Downloader to avoid circular import."""
    from transcribe_reader.services.s3_downloader import S3Downloader

    return S3Downloader(s3_client)


def _create_gitlab_uploader(gitlab_client: GitLabClient):
    """Factory for GitLabUploader to avoid circular import."""
    from transcribe_reader.services.gitlab_uploader import GitLabUploader

    return GitLabUploader(gitlab_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # AWS Session
    session = providers.Singleton(_create_session)

    # S3
    s3_boto_client = providers.Singleton(
        lambda session: session.client("s3"),
        session=session,
    )

    s3_client = providers.Singleton(
        S3Client,
        client=s3_boto_client,
    )

    s3_downloader = providers.Singleton(
        _create_s3_downloader,
        s3_client=s3_client,
    )

    # GitLab
    gitlab_client = providers.Singleton(_create_gitlab_client)

    gitlab_uploader = providers.Singleton(
        _create_gitlab_uploader,
        gitlab_client=gitlab_client,
    )
