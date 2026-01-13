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
    # Sample event
    event = {
        "test": "data",
        "message": "Hello from local test",
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
