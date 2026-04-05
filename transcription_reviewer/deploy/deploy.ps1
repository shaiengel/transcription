$ErrorActionPreference = "Stop"

# Configuration
$LAMBDA_FUNCTION_NAME = "transcription-reviewer"
$AWS_REGION = "us-east-1"

# Navigate to project root
Push-Location (Split-Path $PSScriptRoot -Parent)

try {
    Write-Host "=== Exporting requirements ==="
    uv export --no-hashes --no-dev --no-annotate --no-emit-project -o requirements.txt

    Write-Host "=== Building Lambda package ==="
    python deploy/build.py

    Write-Host "=== Uploading to AWS Lambda ==="
    aws lambda update-function-code `
        --function-name $LAMBDA_FUNCTION_NAME `
        --zip-file fileb://lambda_function.zip `
        --region $AWS_REGION --profile portal

    Write-Host "=== Deployment complete ==="
}
finally {
    Pop-Location
}
