import json
import logging

from post_inference.config import config
from post_inference.handlers.process import process_batch_output
from post_inference.infrastructure.s3_client import S3Client
from post_inference.infrastructure.sqs_client import SQSClient
from post_inference.models.post_processing import PostProcessing
from post_inference.services.batch_result_processor import BatchResultProcessor

logger = logging.getLogger(__name__)


class BedrockPostProcessing(PostProcessing):

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        bedrock_client,
        batch_result_processor: BatchResultProcessor,
    ):
        self._s3_client = s3_client
        self._sqs_client = sqs_client
        self._bedrock_client = bedrock_client
        self._batch_result_processor = batch_result_processor

    def authenticate(self, event: dict) -> None:
        pass

    def process(self, event: dict) -> dict:
        detail = event.get("detail", {})
        job_arn = detail.get("batchJobArn")

        if not job_arn:
            logger.error("No batchJobArn in event detail")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No batchJobArn in event"}),
            }

        logger.info("Processing batch job: %s", job_arn)

        result = process_batch_output(
            job_arn=job_arn,
            bedrock_client=self._bedrock_client,
            s3_client=self._s3_client,
            sqs_client=self._sqs_client,
            batch_result_processor=self._batch_result_processor,
            transcription_bucket=config.transcription_bucket,
            output_bucket=config.output_bucket,
            audio_bucket=config.audio_bucket,
            sqs_queue_url=config.sqs_queue_url,
        )

        response_body = {
            "message": "Post-inference processing completed",
            "total_records": result.total_records,
            "processed": result.processed,
            "failed": result.failed,
            "cleaned_up": result.cleaned_up,
        }

        logger.info("Processing completed: %s", response_body)
        return {"statusCode": 200, "body": json.dumps(response_body)}
