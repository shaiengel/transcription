import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from audio_manager.infrastructure.s3_client import S3Client


def _create_session() -> boto3.Session:
    """Create boto3 session using profile from environment."""
    load_dotenv()
    profile = os.getenv("AWS_PROFILE", "default")
    return boto3.Session(profile_name=profile)


def _create_s3_uploader(s3_client: S3Client):
    """Factory for S3Uploader to avoid circular import."""
    from audio_manager.services.s3_uploader import S3Uploader

    return S3Uploader(s3_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    session = providers.Singleton(_create_session)

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
