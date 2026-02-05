"""Dependency injection container for the application."""

import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.infrastructure.bedrock_client import BedrockClient
from transcription_reviewer.infrastructure.bedrock_batch_client import BedrockBatchClient
from transcription_reviewer.services.token_counter import TokenCounter


def _create_session() -> boto3.Session:
    """Create boto3 session.

    In Lambda: Uses execution role automatically.
    Locally: Uses AWS_PROFILE_REVIEWER from environment.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    # In Lambda, use default credentials from execution role
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=region)

    # For local testing, use AWS profile
    profile = os.getenv("AWS_PROFILE_REVIEWER", "default")
    return boto3.Session(profile_name=profile)


def _create_s3_reader(s3_client: S3Client):
    """Factory for S3Reader to avoid circular import."""
    from transcription_reviewer.services.s3_reader import S3Reader

    return S3Reader(s3_client)


def _create_transcription_fixer(bedrock_client: BedrockClient, s3_client: S3Client):
    """Factory for TranscriptionFixer to avoid circular import."""
    from transcription_reviewer.services.transcription_fixer import TranscriptionFixer

    model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")
    return TranscriptionFixer(bedrock_client, s3_client, model_id)


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

    # Bedrock dependency chain
    bedrock_boto_client = providers.Singleton(
        lambda session: session.client("bedrock-runtime"),
        session=session,
    )

    bedrock_client = providers.Singleton(
        BedrockClient,
        client=bedrock_boto_client,
    )

    transcription_fixer = providers.Singleton(
        _create_transcription_fixer,
        bedrock_client=bedrock_client,
        s3_client=s3_client,
    )

    # Bedrock batch client (uses "bedrock" not "bedrock-runtime")
    bedrock_batch_boto_client = providers.Singleton(
        lambda session: session.client("bedrock"),
        session=session,
    )

    bedrock_batch_client = providers.Singleton(
        BedrockBatchClient,
        client=bedrock_batch_boto_client,
    )

    # Token counter (uses Anthropic SDK with Bedrock)
    token_counter = providers.Singleton(
        TokenCounter,
        model_id=os.getenv("BATCH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0"),
        region=os.getenv("AWS_REGION", "us-east-1"),
    )
