$ErrorActionPreference = "Stop"

$LAMBDA_FUNCTION_NAME = "lambda-metric"
$AWS_REGION = "us-east-1"
$AWS_PROFILE = "default"

Push-Location "$PSScriptRoot/.."

try {
    Write-Host "=== Exporting requirements ===" -ForegroundColor Cyan
    uv export --no-hashes --no-dev --no-annotate --no-emit-project -o requirements.txt

    Write-Host "=== Building Lambda package ===" -ForegroundColor Cyan
    python deploy/build.py

    Write-Host "=== Uploading to AWS Lambda ===" -ForegroundColor Cyan
    aws lambda update-function-code `
        --function-name $LAMBDA_FUNCTION_NAME `
        --zip-file fileb://lambda_function.zip `
        --region $AWS_REGION `
        --profile $AWS_PROFILE

    Write-Host "=== Deployment complete ===" -ForegroundColor Green
}
finally {
    Pop-Location
}
