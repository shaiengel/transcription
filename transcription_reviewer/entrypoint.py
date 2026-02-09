"""Docker entrypoint for running the transcription reviewer."""

import json
import logging
import os

from dotenv import load_dotenv

# Load env from mounted file
env_path = os.getenv("DOTENV_PATH", "/app/.env")
load_dotenv(env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from transcription_reviewer.handler import lambda_handler

if __name__ == "__main__":
    event = {"source": "docker"}
    response = lambda_handler(event, None)
    print(json.dumps(response, indent=2))
