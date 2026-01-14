"""Dependency injection container for the application."""

import os
from pathlib import Path

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer
from dotenv import load_dotenv

from gpu_instance.infrastructure.s3_client import S3Client
from gpu_instance.infrastructure.sqs_client import SQSClient
from gpu_instance.infrastructure.vtt_formatter import VttFormatter
from gpu_instance.infrastructure.text_formatter import TextFormatter
from gpu_instance.infrastructure.timed_text_formatter import TimedTextFormatter

# Load .env file from project root
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)


def _create_session() -> boto3.Session:
    """Create boto3 session with assumed role."""
    region = os.getenv("AWS_REGION", "us-east-1")
    role_arn = os.getenv(
        "AWS_ROLE_ARN", "arn:aws:iam::707072965202:role/gpu-transcription-role"
    )

    sts = boto3.client("sts", region_name=region)
    assumed = sts.assume_role(RoleArn=role_arn, RoleSessionName="gpu-worker")
    credentials = assumed["Credentials"]

    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def _create_s3_downloader(s3_client: S3Client):
    """Factory for S3Downloader to avoid circular import."""
    from gpu_instance.services.s3_downloader import S3Downloader

    return S3Downloader(s3_client)


def _create_s3_uploader(s3_client: S3Client):
    """Factory for S3Uploader to avoid circular import."""
    from gpu_instance.services.s3_uploader import S3Uploader

    return S3Uploader(s3_client)


def _create_sqs_receiver(sqs_client: SQSClient):
    """Factory for SQSReceiver to avoid circular import."""
    from gpu_instance.services.sqs_receiver import SQSReceiver

    return SQSReceiver(sqs_client)


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # Session with assumed role
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

    # Formatters
    formatters = providers.List(
        # providers.Singleton(VttFormatter),
        # providers.Singleton(TextFormatter),
        providers.Singleton(TimedTextFormatter),
    )
