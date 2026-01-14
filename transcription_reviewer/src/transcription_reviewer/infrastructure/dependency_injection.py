"""Dependency injection container for the application."""

import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from transcription_reviewer.infrastructure.s3_client import S3Client


def _create_session() -> boto3.Session:
    """Create boto3 session.

    In Lambda: Uses execution role automatically.
    Locally: Uses AWS_PROFILE from environment.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    # In Lambda, use default credentials from execution role
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=region)

    # For local testing, use AWS profile
    profile = os.getenv("AWS_PROFILE", "default")
    return boto3.Session(profile_name=profile, region_name=region)


def _create_s3_reader(s3_client: S3Client):
    """Factory for S3Reader to avoid circular import."""
    from transcription_reviewer.services.s3_reader import S3Reader

    return S3Reader(s3_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # Session (Lambda execution role or local AWS profile)
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

    s3_reader = providers.Singleton(
        _create_s3_reader,
        s3_client=s3_client,
    )
