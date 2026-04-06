import os
from pathlib import Path

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from audio_manager.infrastructure.gitlab_client import GitLabClient
from audio_manager.infrastructure.s3_client import S3Client
from audio_manager.infrastructure.sqs_client import SQSClient

env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path, override=True)

def _create_session() -> boto3.Session:
    """Create boto3 session using profile from environment."""
    
    profile = os.getenv("AWS_PROFILE", "portal")
    return boto3.Session(profile_name=profile)


def _create_database_media_source():
    """Factory for DatabaseMediaSource."""
    from audio_manager.infrastructure.database_media_source import DatabaseMediaSource

    return DatabaseMediaSource()


def _create_local_disk_media_source():
    """Factory for LocalDiskMediaSource."""
    from audio_manager.infrastructure.local_disk_media_source import LocalDiskMediaSource

    return LocalDiskMediaSource()


def _create_s3_uploader(s3_client: S3Client):
    """Factory for S3Uploader to avoid circular import."""
    from audio_manager.services.s3_uploader import S3Uploader

    return S3Uploader(s3_client)


def _create_sqs_publisher(sqs_client: SQSClient):
    """Factory for SQSPublisher to avoid circular import."""
    from audio_manager.services.sqs_publisher import SQSPublisher

    return SQSPublisher(sqs_client)


def _create_gitlab_client() -> GitLabClient | None:
    """Factory for GitLabClient."""
    url = os.getenv("GITLAB_URL", "https://gitlab.com")
    project_id = os.getenv("GITLAB_PROJECT_ID", "")
    private_token = os.getenv("GITLAB_PRIVATE_TOKEN", "")

    if not private_token or not project_id:
        return None

    return GitLabClient(url, private_token, project_id)


def _create_sefaria_url_fetcher():
    """Factory for SefariaUrlDafTextFetcher."""
    from audio_manager.services.sefaria_fetcher import SefariaUrlDafTextFetcher

    return SefariaUrlDafTextFetcher()


def _create_gitlab_fetcher(gitlab_client: GitLabClient | None):
    """Factory for GitLabDafTextFetcher."""
    if not gitlab_client:
        return None
    from audio_manager.services.sefaria_fetcher import GitLabDafTextFetcher

    branch = os.getenv("GITLAB_BRANCH", "main")
    return GitLabDafTextFetcher(gitlab_client, branch)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # =========================================================================
    # Media Source - Comment out one of the following two lines:
    # =========================================================================
    media_source = providers.Singleton(_create_database_media_source)  # From MSSQL DB
    #media_source = providers.Singleton(_create_local_disk_media_source)  # From local disk

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

    s3_uploader = providers.Singleton(
        _create_s3_uploader,
        s3_client=s3_client,
    )

    # SQS
    sqs_boto_client = providers.Singleton(
        lambda session: session.client("sqs"),
        session=session,
    )

    sqs_client = providers.Singleton(
        SQSClient,
        client=sqs_boto_client,
    )

    sqs_publisher = providers.Singleton(
        _create_sqs_publisher,
        sqs_client=sqs_client,
    )

    # GitLab
    gitlab_client = providers.Singleton(_create_gitlab_client)

    # =========================================================================
    # Daf Text Fetcher - Comment out one of the following two lines:
    # =========================================================================
    daf_text_fetcher = providers.Singleton(_create_sefaria_url_fetcher)
    # daf_text_fetcher = providers.Singleton(_create_gitlab_fetcher, gitlab_client=gitlab_client)
