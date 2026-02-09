"""Dependency injection container for the application."""

import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from post_inference.infrastructure.s3_client import S3Client
from post_inference.infrastructure.sqs_client import SQSClient


def _create_session() -> boto3.Session:
    """Create boto3 session.

    In Lambda: Uses execution role automatically.
    Locally: Uses AWS_PROFILE from environment.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=region)

    profile = os.getenv("AWS_PROFILE_POST_REVIEWER", "default")
    return boto3.Session(profile_name=profile)


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

    # Bedrock client (not bedrock-runtime) for get_model_invocation_job
    bedrock_boto_client = providers.Singleton(
        lambda session: session.client("bedrock"),
        session=session,
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
