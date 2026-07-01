"""Dependency injection container for the application."""

import os

import boto3
from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from transcription_reviewer.config import Config
from transcription_reviewer.infrastructure.dynamodb_client import DynamoDBClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.infrastructure.sqs_client import SQSClient
from transcription_reviewer.infrastructure.bedrock_client import BedrockClient
from transcription_reviewer.infrastructure.bedrock_batch_client import BedrockBatchClient
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.models.review_orchestrator import ReviewOrchestrator
from transcription_reviewer.services.gemini_pipeline import GeminiPipeline
from transcription_reviewer.services.fix_tracker import FixTrackerService

# Lazy imports for Bedrock pipeline (avoids tiktoken dependency when using Gemini)
# from transcription_reviewer.services.token_counter import TokenCounter
# from transcription_reviewer.services.bedrock_batch_pipeline import BedrockBatchPipeline


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
    profile = os.getenv("AWS_PROFILE_REVIEWER", "reviewer")
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


def _create_bedrock_pipeline(
    s3_client: S3Client,
    bedrock_batch_client: BedrockBatchClient,
) -> LLMPipeline:
    """Create Bedrock batch pipeline (lazy import to avoid tiktoken dependency)."""
    from transcription_reviewer.services.token_counter import TokenCounter
    from transcription_reviewer.services.bedrock_batch_pipeline import BedrockBatchPipeline

    config = Config()
    token_counter = TokenCounter(
        model_id=config.batch_model_id,
        region=config.aws_region,
    )
    return BedrockBatchPipeline(
        s3_client=s3_client,
        bedrock_batch_client=bedrock_batch_client,
        token_counter=token_counter,
        bucket=config.transcription_bucket,
        batch_model_id=config.batch_model_id,
        batch_role_arn=config.batch_role_arn,
        min_entries=config.min_entries,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
    )


def _create_dynamo_reader(dynamodb_client: DynamoDBClient):
    """Factory for DynamoReader."""
    from transcription_reviewer.services.dynamo_reader import DynamoReader

    config = Config()
    return DynamoReader(dynamodb_client, config.media_table)


def _create_fix_tracker(dynamodb_client: DynamoDBClient) -> FixTrackerService:
    config = Config()
    return FixTrackerService(dynamodb_client, config.fix_tracker_table)


def _create_gemini_pipeline(
    s3_client: S3Client,
    sqs_client: SQSClient,
    fix_tracker: FixTrackerService,
) -> LLMPipeline:
    """Create Gemini pipeline."""
    return GeminiPipeline(
        s3_client=s3_client,
        sqs_client=sqs_client,
        fix_tracker=fix_tracker,
    )


def _create_on_demand_orchestrator(s3_reader, pipeline, transcription_fixer, dynamo_reader):
    """Factory for OnDemandOrchestrator."""
    from transcription_reviewer.handlers.on_demand_orchestrator import OnDemandOrchestrator

    config = Config()
    return OnDemandOrchestrator(
        s3_reader=s3_reader,
        pipeline=pipeline,
        transcription_fixer=transcription_fixer,
        dynamo_reader=dynamo_reader,
        bucket=config.transcription_bucket,
    )


def _create_gemini_batch_service(dynamodb_client, s3_client):
    """Factory for GeminiBatchService."""
    from transcription_reviewer.services.gemini_batch_service import GeminiBatchService

    return GeminiBatchService(
        dynamodb_client=dynamodb_client,
        s3_client=s3_client,
    )


def _create_gemini_batch_orchestrator(
    s3_reader, s3_client, dynamo_reader, transcription_fixer, batch_service
):
    """Factory for GeminiBatchOrchestrator."""
    from transcription_reviewer.handlers.gemini_batch_orchestrator import (
        GeminiBatchOrchestrator,
    )

    config = Config()
    return GeminiBatchOrchestrator(
        s3_reader=s3_reader,
        s3_client=s3_client,
        dynamo_reader=dynamo_reader,
        transcription_fixer=transcription_fixer,
        batch_service=batch_service,
        bucket=config.transcription_bucket,
    )


def _create_gemini_batch_retrigger_orchestrator(
    s3_client, sqs_client, dynamo_reader, transcription_fixer, batch_service, batch_job_id
):
    """Factory for GeminiBatchRetriggerOrchestrator."""
    from transcription_reviewer.handlers.gemini_batch_retrigger_orchestrator import (
        GeminiBatchRetriggerOrchestrator,
    )

    return GeminiBatchRetriggerOrchestrator(
        s3_client=s3_client,
        sqs_client=sqs_client,
        dynamo_reader=dynamo_reader,
        transcription_fixer=transcription_fixer,
        batch_service=batch_service,
        batch_job_id=batch_job_id,
    )


class DependenciesContainer(DeclarativeContainer):
    """DI container for the application."""

    # Session (Lambda execution role or local AWS profile)
    session = providers.Singleton(_create_session)

    # DynamoDB dependency chain
    dynamodb_boto_client = providers.Singleton(
        lambda session: session.client("dynamodb"),
        session=session,
    )

    dynamodb_client = providers.Singleton(
        DynamoDBClient,
        client=dynamodb_boto_client,
    )

    dynamo_reader = providers.Singleton(
        _create_dynamo_reader,
        dynamodb_client=dynamodb_client,
    )

    fix_tracker = providers.Singleton(
        _create_fix_tracker,
        dynamodb_client=dynamodb_client,
    )

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
    # token_counter = providers.Singleton(
    #     TokenCounter,
    #     model_id=os.getenv("BATCH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0"),
    #     region=os.getenv("AWS_REGION", "us-east-1"),
    # )

    # SQS dependency chain
    sqs_boto_client = providers.Singleton(
        lambda session: session.client("sqs"),
        session=session,
    )

    sqs_client = providers.Singleton(
        SQSClient,
        client=sqs_boto_client,
    )

    # LLM Pipeline - Choose one by commenting/uncommenting:

    # Option 1: AWS Bedrock Batch (AWS_OPUS4.5)
    # llm_pipeline = providers.Singleton(
    #     _create_bedrock_pipeline,
    #     s3_client=s3_client,
    #     bedrock_batch_client=bedrock_batch_client,
    # )

    # Option 2: Google Gemini (GEMINI2.5)
    llm_pipeline = providers.Singleton(
        _create_gemini_pipeline,
        s3_client=s3_client,
        sqs_client=sqs_client,
        fix_tracker=fix_tracker,
    )

    # --- Orchestrators ---

    on_demand_orchestrator = providers.Factory(
        _create_on_demand_orchestrator,
        s3_reader=s3_reader,
        pipeline=llm_pipeline,
        transcription_fixer=transcription_fixer,
        dynamo_reader=dynamo_reader,
    )

    gemini_batch_service = providers.Singleton(
        _create_gemini_batch_service,
        dynamodb_client=dynamodb_client,
        s3_client=s3_client,
    )

    gemini_batch_orchestrator = providers.Factory(
        _create_gemini_batch_orchestrator,
        s3_reader=s3_reader,
        s3_client=s3_client,
        dynamo_reader=dynamo_reader,
        transcription_fixer=transcription_fixer,
        batch_service=gemini_batch_service,
    )

    gemini_batch_retrigger_orchestrator = providers.Factory(
        _create_gemini_batch_retrigger_orchestrator,
        s3_client=s3_client,
        sqs_client=sqs_client,
        dynamo_reader=dynamo_reader,
        transcription_fixer=transcription_fixer,
        batch_service=gemini_batch_service,
        batch_job_id="",  # overridden at call site
    )

    # Config-based orchestrator selection (initial trigger)
    orchestrator = providers.Selector(
        providers.Callable(lambda: Config().llm_backend),
        GEMINI_BATCH=gemini_batch_orchestrator,
        **{
            "AWS_OPUS4.5": on_demand_orchestrator,
            "GEMINI2.5": on_demand_orchestrator,
        },
    )
