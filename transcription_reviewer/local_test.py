"""Local test script for the Lambda handler."""

import json
import logging

from transcription_reviewer.handler import lambda_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main():
    """Run a local test of the Lambda handler."""
    # Sample CloudWatch Alarm event (when ASG scales to 0)
    event = {
        "source": "aws.cloudwatch",
        "alarmArn": "arn:aws:cloudwatch:us-east-1:123456789:alarm:asg-scale-to-zero",
        "alarmData": {
            "alarmName": "asg-scale-to-zero",
            "state": {
                "value": "ALARM",
                "reason": "ASG instances count reached 0",
            },
            "previousState": {
                "value": "OK",
            },
        },
    }

    # Mock context (Lambda context object)
    context = None

    # Invoke handler
    response = lambda_handler(event, context)

    print("\n" + "=" * 50)
    print("Response:")
    print(json.dumps(response, indent=2))
    print("=" * 50)


if __name__ == "__main__":
    main()
