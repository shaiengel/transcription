# Python Lambda Project Structure - Best Practices

Based on the `transcription_reviewer` Lambda function pattern.

## 1. Project Structure Template

```
project_name/
├── pyproject.toml              # uv configuration
├── uv.lock                     # Locked dependencies
├── local_test.py              # Local testing script
├── .env.jinja                 # Environment template
└── src/project_name/
    ├── __init__.py            # Package exports
    ├── handler.py             # Lambda entry point
    ├── config.py              # Configuration class
    ├── models/
    │   ├── __init__.py
    │   └── schemas.py         # Pydantic models
    ├── handlers/
    │   ├── __init__.py
    │   └── business_logic.py  # Main workflow
    ├── services/
    │   ├── __init__.py
    │   └── service_name.py    # Business services
    ├── infrastructure/
    │   ├── __init__.py
    │   ├── dependency_injection.py  # DI container
    │   └── aws_client.py      # AWS wrappers
    └── utils/
        ├── __init__.py
        └── helpers.py         # Utility functions
```

## 2. File Organization Principles

**models/** - Data structures and schemas
- Pydantic models for validation
- Dataclasses for internal data structures
- Type definitions

**services/** - Business logic
- Domain-specific operations
- Reusable business functions
- Independent of infrastructure

**infrastructure/** - AWS integrations
- AWS client wrappers (S3, Bedrock, etc.)
- Dependency injection container
- External service integrations

**handlers/** - Event orchestration
- Lambda event handling
- Workflow coordination
- Service composition

**utils/** - Shared utilities
- Helper functions
- Format converters
- Common operations

## 3. Dependency Injection Pattern

### Setup DI Container

**File:** `src/project_name/infrastructure/dependency_injection.py`

```python
from dependency_injector import containers, providers
import boto3
import os

class DependenciesContainer(containers.DeclarativeContainer):
    """DI container for managing dependencies."""

    # Session detection (Lambda vs local)
    session = providers.Singleton(_create_session)

    # AWS clients (3-tier: boto3 → wrapper → service)
    s3_boto_client = providers.Singleton(
        lambda session: session.client("s3"),
        session=session,
    )
    s3_client = providers.Singleton(S3Client, client=s3_boto_client)

    # Services
    my_service = providers.Singleton(
        MyService,
        s3_client=s3_client,
        config_value=providers.Configuration().config_key,
    )

def _create_session() -> boto3.Session:
    """Auto-detect Lambda vs local execution."""
    region = os.getenv("AWS_REGION", "us-east-1")

    # Lambda: Uses execution role
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=region)

    # Local: Uses AWS profile
    profile = os.getenv("AWS_PROFILE", "default")
    return boto3.Session(profile_name=profile, region_name=region)
```

### Usage in Handler

```python
from infrastructure.dependency_injection import DependenciesContainer

def lambda_handler(event: dict, context):
    # Create DI container (once per invocation)
    container = DependenciesContainer()

    # Get services (all singletons)
    my_service = container.my_service()

    # Call business logic
    result = process_event(event, my_service)

    return {"statusCode": 200, "body": result}
```

## 4. pyproject.toml Configuration

```toml
[project]
name = "project-name"
version = "0.1.0"
description = "AWS Lambda function"
requires-python = ">=3.12"
dependencies = [
    "boto3>=1.35.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0.0",
    "dependency-injector>=4.41.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/project_name"]
```

### UV Commands

```bash
# Install dependencies
uv sync

# Run locally
uv run local_test

# Build package
uv build
```

## 5. Infrastructure Layer - AWS Client Wrappers

### Pattern: Wrap boto3 clients

```python
class S3Client:
    """Wrapper around boto3 S3 client."""

    def __init__(self, client):
        self._client = client

    def get_object_content(self, bucket: str, key: str) -> str | None:
        """Get S3 object content with error handling."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read().decode("utf-8")
        except ClientError as e:
            logger.error("Failed to get %s: %s", key, e)
            return None

    def put_object_content(self, bucket: str, key: str, content: str) -> bool:
        """Put S3 object with error handling."""
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content.encode("utf-8"),
            )
            return True
        except ClientError as e:
            logger.error("Failed to put %s: %s", key, e)
            return False
```

**Benefits:**
- Centralized error handling
- Consistent logging
- Type hints
- Testable (can mock wrapper)

## 6. Service Layer - Business Logic

```python
class MyService:
    """Business logic service."""

    def __init__(self, s3_client: S3Client, config_value: str):
        self._s3_client = s3_client
        self._config_value = config_value

    def process_data(self, bucket: str, key: str) -> str | None:
        """Main business logic."""
        # Get data from S3
        content = self._s3_client.get_object_content(bucket, key)
        if not content:
            return None

        # Process data
        result = self._transform(content)

        # Save result
        output_key = f"processed/{key}"
        self._s3_client.put_object_content(bucket, output_key, result)

        return result

    def _transform(self, content: str) -> str:
        """Private helper method."""
        return content.upper()
```

## 7. Handler Pattern - Lambda Entry Point

```python
def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point."""

    # Initialize DI container
    container = DependenciesContainer()

    # Get services
    my_service = container.my_service()

    # Parse event
    bucket = event.get("bucket")
    key = event.get("key")

    # Call business logic
    result = my_service.process_data(bucket, key)

    # Return response
    return {
        "statusCode": 200 if result else 500,
        "body": json.dumps({"result": result}),
    }
```

**Package Export** (`src/project_name/__init__.py`):

```python
from .handler import lambda_handler

__all__ = ["lambda_handler"]
```

## 8. Configuration Management

```python
from dataclasses import dataclass
import os

@dataclass
class Config:
    """Application configuration from environment."""

    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    bucket_name: str = os.getenv("BUCKET_NAME", "")

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.bucket_name:
            raise ValueError("BUCKET_NAME is required")

config = Config()
config.validate()
```

**Environment Template** (`.env.jinja`):

```jinja
# AWS Configuration
AWS_REGION={{ aws.region }}
AWS_PROFILE={{ aws.profile }}

# S3 Configuration
BUCKET_NAME={{ s3.bucket_name }}

# Custom Configuration
MY_CONFIG_VALUE={{ custom.value }}
```

## 9. Local Testing

**local_test.py:**

```python
from dotenv import load_dotenv
from project_name import lambda_handler
import json

def main():
    # Load .env file
    load_dotenv()

    # Sample event
    event = {
        "bucket": "my-bucket",
        "key": "data.txt",
    }

    # Run handler
    response = lambda_handler(event, None)

    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()
```

**Run:**

```bash
# Create .env from template
cp .env.jinja .env
# Edit .env with real values

# Run test
uv run local_test
```

## 10. Deployment

### Build Lambda Package

```bash
# Build wheel
uv build

# Create Lambda package
mkdir lambda_package
cd lambda_package
pip install ../dist/project_name-0.1.0-py3-none-any.whl -t .

# Zip for upload
zip -r ../lambda_function.zip .
```

### Lambda Configuration

| Setting | Value |
|---------|-------|
| Handler | `project_name.handler.lambda_handler` |
| Runtime | Python 3.12 |
| Memory | 512 MB |
| Timeout | 60 seconds |
| Environment Variables | See config section |

### IAM Role Requirements

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::my-bucket/*"
        }
    ]
}
```

## 11. Best Practices

### ✅ DO

1. **Use dependency injection** - Makes code testable and decoupled
2. **Wrap AWS clients** - Centralize error handling and logging
3. **Separate concerns** - models, services, infrastructure, handlers
4. **Type hints everywhere** - Improves IDE support and catches errors
5. **Use Pydantic for validation** - Auto-validate event data
6. **Environment-based config** - Use .env for local, Lambda env vars for production
7. **Singleton services** - Create once per invocation via DI
8. **Return structured responses** - Always include statusCode and body

### ❌ DON'T

1. **Don't create clients in services** - Inject them via DI
2. **Don't use global state** - Each invocation should be independent
3. **Don't hardcode config** - Use environment variables
4. **Don't mix business logic and AWS calls** - Separate layers
5. **Don't catch all exceptions silently** - Log errors properly
6. **Don't reinvent wheels** - Use existing patterns (DI, wrappers)

## 12. Testing Pattern

```python
# tests/test_service.py
from unittest.mock import Mock
import pytest

def test_my_service():
    # Mock dependencies
    mock_s3 = Mock()
    mock_s3.get_object_content.return_value = "test content"

    # Create service with mocks
    service = MyService(s3_client=mock_s3, config_value="test")

    # Call service
    result = service.process_data("bucket", "key")

    # Assert
    assert result == "TEST CONTENT"
    mock_s3.put_object_content.assert_called_once()
```

## 13. Common Patterns

### Pattern 1: Three-Tier Architecture

```
boto3 client → Wrapper (error handling) → Service (business logic)
```

### Pattern 2: Factory Functions for Circular Imports

```python
def _create_service(dependency):
    """Defer import to avoid circular dependency."""
    from services.my_service import MyService
    return MyService(dependency)
```

### Pattern 3: Credential Auto-Detection

```python
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    # Lambda: use execution role
else:
    # Local: use AWS profile
```

### Pattern 4: Batch Processing with Padding

```python
# Always pad to minimum for pricing optimization
while len(entries) < MIN_ENTRIES:
    entries.append(create_dummy_entry())
```

## 14. Example: Complete Minimal Lambda

```
my_lambda/
├── pyproject.toml
├── local_test.py
└── src/my_lambda/
    ├── __init__.py (exports lambda_handler)
    ├── handler.py (DI container → business logic)
    ├── config.py (env vars)
    ├── models/schemas.py (Pydantic)
    ├── services/processor.py (business logic)
    └── infrastructure/
        ├── dependency_injection.py (DI container)
        └── s3_client.py (boto3 wrapper)
```

**Minimal handler.py:**

```python
from infrastructure.dependency_injection import DependenciesContainer
import json

def lambda_handler(event: dict, context):
    container = DependenciesContainer()
    processor = container.processor()
    result = processor.process(event)
    return {"statusCode": 200, "body": json.dumps(result)}
```

**That's it!** This structure scales from simple to complex Lambdas.

---

## Summary

This pattern provides:
- ✅ Clear separation of concerns
- ✅ Easy local testing
- ✅ Testable code (via DI)
- ✅ Type safety
- ✅ Consistent error handling
- ✅ Environment-aware configuration
- ✅ Scalable from simple to complex

Use this as a template for all Python Lambda projects.
