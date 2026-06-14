$ErrorActionPreference = "Stop"

# Configuration
$LAMBDA_FUNCTION_NAME = "post-inference"
$AWS_REGION = "us-east-1"

# Keys excluded from Lambda environment (secrets or local-dev-only)
$EXCLUDE_KEYS = @("GOOGLE_API_KEY", "AWS_PROFILE_POST_REVIEWER", "AWS_REGION")

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

    Write-Host "=== Waiting for function update ==="
    aws lambda wait function-updated `
        --function-name $LAMBDA_FUNCTION_NAME `
        --region $AWS_REGION --profile portal

    Write-Host "=== Reading .env and uploading environment variables ==="
    $envVars = @{}
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            $key = $parts[0].Trim()
            $value = if ($parts.Length -gt 1) { $parts[1] } else { "" }
            if ($key -notin $EXCLUDE_KEYS) {
                $envVars[$key] = $value
            }
        }
    }
    $envJson = (@{ Variables = $envVars } | ConvertTo-Json -Compress)
    aws lambda update-function-configuration `
        --function-name $LAMBDA_FUNCTION_NAME `
        --environment $envJson `
        --region $AWS_REGION --profile portal

    Write-Host "=== Deployment complete ==="
}
finally {
    Pop-Location
}
