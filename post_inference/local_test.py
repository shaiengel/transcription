"""Local test script for the post-inference Lambda handler."""

import json
import logging

from post_inference.handler import lambda_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main():
    """Run a local test of the Lambda handler."""
    # Sample EventBridge event for Bedrock batch job completion
    event = {
        "version": "0",
        "id": "a1b2c3d4",
        "detail-type": "Batch Inference Job State Change",
        "source": "aws.bedrock",
        "account": "707072965202",
        "region": "us-east-1",
        "resources": ["arn:aws:bedrock:us-east-1:707072965202:model-invocation-job/ra2zk4a8h696"],
        "detail": {
            "batchJobArn": "arn:aws:bedrock:us-east-1:707072965202:model-invocation-job/ra2zk4a8h696",
            "batchJobName": "transcription-fix-1c5a5618 ",
            "status": "Completed",
        },
    }

    context = None

    response = lambda_handler(event, context)

    print("\n" + "=" * 50)
    print("Response:")
    print(json.dumps(response, indent=2))
    print("=" * 50)


if __name__ == "__main__":
    main()
