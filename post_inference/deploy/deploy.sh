#!/bin/bash
set -e

# Configuration
LAMBDA_FUNCTION_NAME="post-inference"
AWS_REGION="us-east-1"

# Keys excluded from Lambda environment (secrets or local-dev-only)
EXCLUDE_KEYS=("GOOGLE_API_KEY" "AWS_PROFILE_POST_REVIEWER" "AWS_REGION")

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
    --region "$AWS_REGION" --profile portal

echo "=== Waiting for function update ==="
aws lambda wait function-updated \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --region "$AWS_REGION" --profile portal

echo "=== Reading .env and uploading environment variables ==="
ENV_JSON=$(python -c "
import json, sys

exclude = set(['GOOGLE_API_KEY', 'AWS_PROFILE_POST_REVIEWER', 'AWS_REGION'])
vars = {}
with open('.env') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if key not in exclude:
            vars[key] = value
print(json.dumps({'Variables': vars}))
")
aws lambda update-function-configuration \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --environment "$ENV_JSON" \
    --region "$AWS_REGION" --profile portal

echo "=== Deployment complete ==="
