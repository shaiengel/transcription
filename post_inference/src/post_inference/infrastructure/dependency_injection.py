import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from post_inference.config import config
from post_inference.handlers.bedrock_post_processing import BedrockPostProcessing
from post_inference.handlers.gemini_post_processing import GeminiPostProcessing
from post_inference.infrastructure.dynamodb_client import DynamoDBClient
from post_inference.infrastructure.s3_client import S3Client
from post_inference.infrastructure.sqs_client import SQSClient
from post_inference.services.batch_result_processor import BatchResultProcessor
# Commented out: dynamic webhook authentication (JWT/JWKS)
# from post_inference.services.jwt_verifier import JWTVerifier


def _create_session() -> boto3.Session:
    """Create boto3 session.

    In Lambda: Uses execution role automatically.
    Locally: Uses AWS_PROFILE from environment.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=region)

    profile = os.getenv("AWS_PROFILE_POST_REVIEWER", "post_reviewer")
    return boto3.Session(profile_name=profile)


class DependenciesContainer(DeclarativeContainer):

    session = providers.Singleton(_create_session)

    s3_boto_client = providers.Singleton(
        lambda session: session.client("s3"),
        session=session,
    )

    s3_client = providers.Singleton(
        S3Client,
        client=s3_boto_client,
    )

    bedrock_boto_client = providers.Singleton(
        lambda session: session.client("bedrock"),
        session=session,
    )

    sqs_boto_client = providers.Singleton(
        lambda session: session.client("sqs"),
        session=session,
    )

    sqs_client = providers.Singleton(
        SQSClient,
        client=sqs_boto_client,
    )

    batch_result_processor = providers.Singleton(
        BatchResultProcessor,
        s3_client=s3_client,
    )

    # --- Gemini webhook providers ---
    dynamodb_boto_client = providers.Singleton(
        lambda session: session.client("dynamodb"),
        session=session,
    )

    dynamodb_client = providers.Singleton(
        DynamoDBClient,
        client=dynamodb_boto_client,
    )

    lambda_boto_client = providers.Singleton(
        lambda session: session.client("lambda"),
        session=session,
    )

    # Commented out: dynamic webhook authentication (JWT/JWKS)
    # jwt_verifier = providers.Singleton(
    #     JWTVerifier,
    #     jwks_url=config.google_jwks_url,
    #     audience=config.google_webhook_audience,
    # )

    # --- Active implementation (uncomment one) ---
    # post_processor = providers.Singleton(
    #     BedrockPostProcessing,
    #     s3_client=s3_client,
    #     sqs_client=sqs_client,
    #     bedrock_client=bedrock_boto_client,
    #     batch_result_processor=batch_result_processor,
    # )
    post_processor = providers.Singleton(
        GeminiPostProcessing,
        # Commented out: dynamic webhook authentication (JWT/JWKS)
        # jwt_verifier=jwt_verifier,
        dynamodb_client=dynamodb_client,
        lambda_client=lambda_boto_client,
    )
