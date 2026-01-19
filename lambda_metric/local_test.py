"""Local test script for the Lambda handler."""

from dotenv import load_dotenv
load_dotenv()

from lambda_metric.handler import handler


def main():
    """Run a local test of the Lambda handler."""
    event = {
        "version": "0",
        "id": "12345678-1234-1234-1234-123456789012",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "707072965202",
        "time": "2024-01-15T12:00:00Z",
        "region": "us-east-1",
        "resources": [
            "arn:aws:events:us-east-1:707072965202:rule/lambda-metric-schedule"
        ],
        "detail": {},
    }

    context = None

    result = handler(event, context)

    print("\n" + "=" * 50)
    print("Handler completed")
    print(f"Result: {result}")
    print("=" * 50)


if __name__ == "__main__":
    main()
