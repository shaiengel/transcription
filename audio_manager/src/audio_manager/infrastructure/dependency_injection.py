import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from audio_manager.infrastructure.s3_client import S3Client
from audio_manager.infrastructure.sqs_client import SQSClient


def _create_session() -> boto3.Session:
    """Create boto3 session using profile from environment."""
    load_dotenv()
    profile = os.getenv("AWS_PROFILE", "default")
    return boto3.Session(profile_name=profile)


def _create_s3_uploader(s3_client: S3Client):
    """Factory for S3Uploader to avoid circular import."""
    from audio_manager.services.s3_uploader import S3Uploader

    return S3Uploader(s3_client)


def _create_sqs_publisher(sqs_client: SQSClient):
    """Factory for SQSPublisher to avoid circular import."""
    from audio_manager.services.sqs_publisher import SQSPublisher

    return SQSPublisher(sqs_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

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
