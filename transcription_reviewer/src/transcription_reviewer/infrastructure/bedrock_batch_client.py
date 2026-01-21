"""Bedrock batch inference client wrapper."""

import logging
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class BedrockBatchClient:
    """Handles Bedrock batch inference operations."""

    def __init__(self, client: Any):
        """
        Initialize Bedrock batch client wrapper.

        Args:
            client: boto3 bedrock client instance (not bedrock-runtime).
        """
        self._client = client

    def create_batch_job(
        self,
        job_name: str,
        model_id: str,
        role_arn: str,
        input_s3_uri: str,
        output_s3_uri: str,
        timeout_hours: int = 24,
    ) -> str | None:
        """
        Create a batch inference job.

        Args:
            job_name: Unique name for the batch job.
            model_id: Bedrock model ID to use.
            role_arn: IAM role ARN with permissions for batch inference.
            input_s3_uri: S3 URI of the input JSONL file.
            output_s3_uri: S3 URI prefix for output files.
            timeout_hours: Timeout in hours (default 24).

        Returns:
            Job ARN if successful, None otherwise.
        """
        try:
            response = self._client.create_model_invocation_job(
                jobName=job_name,
                modelId=model_id,
                roleArn=role_arn,
                inputDataConfig={
                    "s3InputDataConfig": {
                        "s3Uri": input_s3_uri,
                    }
                },
                outputDataConfig={
                    "s3OutputDataConfig": {
                        "s3Uri": output_s3_uri,
                    }
                },
                timeoutDurationInHours=timeout_hours,
            )

            job_arn = response.get("jobArn")
            logger.info("Created batch job: %s", job_arn)
            return job_arn

        except ClientError as e:
            logger.error("Failed to create batch job: %s", e)
            return None

    def get_job_status(self, job_arn: str) -> dict | None:
        """
        Get the status of a batch inference job.

        Args:
            job_arn: ARN of the batch job.

        Returns:
            Job details dict if successful, None otherwise.
        """
        try:
            response = self._client.get_model_invocation_job(
                jobIdentifier=job_arn,
            )
            return {
                "status": response.get("status"),
                "message": response.get("message"),
                "submitTime": response.get("submitTime"),
                "endTime": response.get("endTime"),
                "inputDataConfig": response.get("inputDataConfig"),
                "outputDataConfig": response.get("outputDataConfig"),
            }
        except ClientError as e:
            logger.error("Failed to get job status: %s", e)
            return None

    def stop_job(self, job_arn: str) -> bool:
        """
        Stop a batch inference job.

        Args:
            job_arn: ARN of the batch job.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client.stop_model_invocation_job(
                jobIdentifier=job_arn,
            )
            logger.info("Stopped batch job: %s", job_arn)
            return True
        except ClientError as e:
            logger.error("Failed to stop batch job: %s", e)
            return False
