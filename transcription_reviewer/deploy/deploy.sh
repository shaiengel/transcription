#!/bin/bash
set -e

# Configuration
LAMBDA_FUNCTION_NAME="transcription-reviewer"
AWS_REGION="us-east-1"

# Navigate to project root
cd "$(dirname "$0")/.."

echo "=== Exporting requirements ==="
uv export --no-hashes --no-dev --no-annotate --no-emit-project -o requirements.txt

echo "=== Building Lambda package ==="
python deploy/build.py

echo "=== Uploading to AWS Lambda ==="
aws lambda update-function-code \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --zip-file fileb://lambda_function.zip \
    --region "$AWS_REGION"

echo "=== Deployment complete ==="
