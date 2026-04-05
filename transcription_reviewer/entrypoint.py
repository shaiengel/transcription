"""Docker entrypoint for running the transcription reviewer."""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path, override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from transcription_reviewer.handler import lambda_handler

if __name__ == "__main__":
    event = {"source": "docker"}
    response = lambda_handler(event, None)
    print(json.dumps(response, indent=2))
