"""Dependency injection container for the application."""

import os
from pathlib import Path

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from gpu_timestamp.infrastructure.s3_client import S3Client
from gpu_timestamp.infrastructure.sqs_client import SQSClient

# Load .env file from project root
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)


def _create_session() -> boto3.Session:
    """Create boto3 session using default credential chain.

    - On EC2: uses instance profile
    - Locally: uses ~/.aws/credentials
    """
    region = os.getenv("AWS_REGION", "us-east-1")
    return boto3.Session(region_name=region)


def _create_s3_downloader(s3_client: S3Client):
    """Factory for S3Downloader to avoid circular import."""
    from gpu_timestamp.services.s3_downloader import S3Downloader

    return S3Downloader(s3_client)


def _create_s3_uploader(s3_client: S3Client):
    """Factory for S3Uploader to avoid circular import."""
    from gpu_timestamp.services.s3_uploader import S3Uploader

    return S3Uploader(s3_client)


def _create_sqs_receiver(sqs_client: SQSClient):
    """Factory for SQSReceiver to avoid circular import."""
    from gpu_timestamp.services.sqs_receiver import SQSReceiver

    return SQSReceiver(sqs_client)


def _create_sqs_sender(sqs_client: SQSClient):
    """Factory for SQSSender to avoid circular import."""
    from gpu_timestamp.services.sqs_sender import SQSSender

    return SQSSender(sqs_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # Session with default credentials
    session = providers.Singleton(_create_session)

    # S3 dependency chain
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

    s3_uploader = providers.Singleton(
        _create_s3_uploader,
        s3_client=s3_client,
    )

    # SQS dependency chain
    sqs_boto_client = providers.Singleton(
        lambda session: session.client("sqs"),
        session=session,
    )

    sqs_client = providers.Singleton(
        SQSClient,
        client=sqs_boto_client,
    )

    sqs_receiver = providers.Singleton(
        _create_sqs_receiver,
        sqs_client=sqs_client,
    )

    sqs_sender = providers.Singleton(
        _create_sqs_sender,
        sqs_client=sqs_client,
    )
