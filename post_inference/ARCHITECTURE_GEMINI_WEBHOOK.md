# Architecture: Gemini Webhook Support in post_inference

## Problem

`post_inference` currently handles only Bedrock batch results via EventBridge. Gemini Batch API delivers results via webhook POST callback. Need to support both providers in the same Lambda using an abstract class, with DI selecting the active implementation.

## Solution: PostProcessing Abstract Class

```
PostProcessing (ABC)
  ├── BedrockPostProcessing
  │     authenticate: no-op (EventBridge is trusted)
  │     process: existing Bedrock flow
  │
  └── GeminiPostProcessing
        authenticate: JWT RS256 verification (kid from env var)
        process: update DynamoDB → invoke transcription-reviewer Lambda
```

DI container wires one implementation; the other is commented out.

---

## PostProcessing API

```python
class PostProcessing(ABC):

    @abstractmethod
    def authenticate(self, event: dict) -> None:
        """Verify event authenticity. Raises AuthenticationError if invalid."""
        ...

    @abstractmethod
    def process(self, event: dict) -> dict:
        """Execute post-processing. Returns {statusCode, body} response."""
        ...
```

### BedrockPostProcessing

```python
class BedrockPostProcessing(PostProcessing):
    """Bedrock batch result processing via EventBridge trigger."""

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        bedrock_client,
        batch_result_processor: BatchResultProcessor,
    ): ...

    def authenticate(self, event: dict) -> None:
        pass  # EventBridge is trusted AWS service

    def process(self, event: dict) -> dict:
        # 1. Extract batchJobArn from event.detail
        # 2. Get output S3 URI via Bedrock API
        # 3. List and parse .jsonl.out file from S3
        # 4. Group split records by stem
        # 5. For each stem:
        #    a. Read .time file from transcription bucket
        #    b. Inject timestamps into fixed text
        #    c. Create VTT
        #    d. Upload .vtt, .txt, .pre-fix.time to output bucket
        #    e. Send SQS notification
        #    f. Clean up source files (audio + transcription buckets)
        # 6. Return {statusCode: 200, body: {total, processed, failed, cleaned_up}}
        ...
```

### GeminiPostProcessing

```python
class GeminiPostProcessing(PostProcessing):
    """Gemini batch webhook receiver. Lightweight — verifies, stores, delegates."""

    def __init__(
        self,
        jwt_verifier: JWTVerifier,
        dynamodb_client: DynamoDBClient,
        lambda_client,
    ): ...

    def authenticate(self, event: dict) -> None:
        # 1. Extract JWT from Webhook-Signature header (case-insensitive)
        # 2. Verify RS256 signature using JWTVerifier
        # 3. Raise AuthenticationError if invalid
        ...

    def process(self, event: dict) -> dict:
        # 1. Parse webhook envelope from event body
        # 2. If type != "batch.succeeded" or no batch_job_id → return 200
        # 3. Look up batch_job_id in BATCH_JOBS_TABLE
        #    - If not found → return 200 (ignore unknown jobs)
        #    - If status == "finished" → return 200 (idempotent, already processed)
        # 4. Immediately set status = "finished" in BATCH_JOBS_TABLE (idempotency gate)
        # 5. Store output_file_uri in BATCH_JOBS_TABLE
        # 6. Invoke transcription-reviewer Lambda async with {"batch_job_id": batch_job_id}
        # 7. Return 200
        ...
```

---

## Lambda Handler

```python
# handler.py
def lambda_handler(event: dict, context) -> dict:
    container = DependenciesContainer()
    processor = container.post_processor()

    try:
        processor.authenticate(event)
    except AuthenticationError as e:
        logger.warning("Authentication failed: %s", e)
        return {"statusCode": 401, "body": json.dumps({"error": str(e)})}

    return processor.process(event)
```

---

## DynamoDB Client

Wrapper class — no raw boto3 calls in handlers. Follows the pattern used in `transcription_reviewer/infrastructure/dynamodb_client.py`.

```python
# infrastructure/dynamodb_client.py
class DynamoDBClient:
    """DynamoDB operations wrapper."""

    def __init__(self, client):
        self._client = client

    def get_item(self, table_name: str, key: dict) -> dict | None:
        """Get item by key. Returns item dict or None if not found."""
        ...

    def update_item(
        self,
        table_name: str,
        key: dict,
        update_expression: str,
        expression_values: dict,
        expression_names: dict | None = None,
    ) -> bool:
        """Update item attributes using raw UpdateExpression API.
        Matches transcription_reviewer DynamoDBClient pattern.
        """
        ...
```

---

## JWT Verification

```python
# services/jwt_verifier.py
class JWTVerifier:
    """Verifies Gemini webhook JWT signatures using Google's JWKS endpoint."""

    _jwks_cache: dict | None = None  # module-level cache for Lambda container reuse

    def __init__(self, jwks_url: str, audience: str):
        self._jwks_url = jwks_url
        self._audience = audience

    def verify(self, token: str) -> dict:
        """Verify RS256 JWT from Webhook-Signature header.
        1. Decode JWT header, extract kid dynamically
        2. Fetch JWKS from Google endpoint (cached)
        3. Find matching public key by kid
        4. Verify RS256 signature and audience claim with PyJWT
        Returns decoded payload dict if valid, raises InvalidTokenError otherwise.
        """
        ...

    def _fetch_jwks(self) -> dict:
        """Fetch and cache Google JWKS. Uses class-level cache
        to survive across warm Lambda invocations."""
        ...
```

---

## Webhook Envelope Format (from Gemini)

```json
{
  "type": "batch.succeeded",
  "version": "v1",
  "timestamp": "2026-01-22T12:00:00Z",
  "data": {
    "id": "batches/batch_123456",
    "output_file_uri": "gs://my-bucket/results.jsonl"
  }
}
```

Other event types (`batch.failed`, `batch.cancelled`, `batch.expired`) are ignored — return 200.

---

## Idempotency Strategy

```
Webhook arrives with batch_job_id
    ↓
Look up in BATCH_JOBS_TABLE
    ├── Not found → return 200 (unknown job, ignore)
    ├── status == "finished" → return 200 (already processed)
    └── status == "submitted" →
          Immediately set status = "finished" (idempotency gate)
          Store output_file_uri
          Invoke transcription-reviewer async
          Return 200
```

All paths return 200 to prevent Gemini from retrying the webhook.

---

## DynamoDB: BATCH_JOBS_TABLE

Same table used by `transcription_reviewer/services/gemini_batch_service.py`.

| Field | Type | Set by |
|-------|------|--------|
| `batch_job_id` (PK) | S | GeminiBatchOrchestrator (initial trigger) |
| `gcs_input_file` | S | GeminiBatchOrchestrator |
| `output_file_uri` | S/NULL | **GeminiPostProcessing** (webhook sets it) |
| `status` | S | GeminiBatchOrchestrator → `"submitted"`, **webhook → `"finished"`** |
| `prompt_caches` | M | GeminiBatchOrchestrator |

---

## Dependency Injection

```python
# infrastructure/dependency_injection.py
class DependenciesContainer(DeclarativeContainer):
    session = providers.Singleton(_create_session)

    # --- Existing providers ---
    s3_boto_client = providers.Singleton(...)
    s3_client = providers.Singleton(S3Client, client=s3_boto_client)
    bedrock_boto_client = providers.Singleton(...)
    sqs_boto_client = providers.Singleton(...)
    sqs_client = providers.Singleton(SQSClient, client=sqs_boto_client)

    # --- New providers (Gemini webhook) ---
    dynamodb_boto_client = providers.Singleton(
        lambda session: session.client("dynamodb"),
        session=session,
    )
    dynamodb_client = providers.Singleton(DynamoDBClient, client=dynamodb_boto_client)

    lambda_boto_client = providers.Singleton(
        lambda session: session.client("lambda"),
        session=session,
    )

    jwt_verifier = providers.Singleton(
        JWTVerifier,
        jwks_url=config.google_jwks_url,
        audience=config.google_webhook_audience,
    )

    batch_result_processor = providers.Singleton(
        BatchResultProcessor, s3_client=s3_client
    )

    # --- Active implementation (uncomment one) ---
    post_processor = providers.Singleton(
        BedrockPostProcessing,
        s3_client=s3_client,
        sqs_client=sqs_client,
        bedrock_client=bedrock_boto_client,
        batch_result_processor=batch_result_processor,
    )
    # post_processor = providers.Singleton(
    #     GeminiPostProcessing,
    #     jwt_verifier=jwt_verifier,
    #     dynamodb_client=dynamodb_client,
    #     lambda_client=lambda_boto_client,
    # )
```

---

## Configuration

### `config.py` additions

```python
@dataclass
class Config:
    # ... existing ...
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    transcription_bucket: str = os.getenv("TRANSCRIPTION_BUCKET", ...)
    output_bucket: str = os.getenv("OUTPUT_BUCKET", ...)
    audio_bucket: str = os.getenv("AUDIO_BUCKET", ...)
    sqs_queue_url: str = os.getenv("SQS_QUEUE_URL", "")

    # Gemini webhook (used when GeminiPostProcessing is active)
    google_webhook_audience: str = os.getenv("GOOGLE_WEBHOOK_AUDIENCE", "")
    google_jwks_url: str = os.getenv(
        "GOOGLE_JWKS_URL",
        "https://generativelanguage.googleapis.com/.well-known/jwks.json",
    )
    batch_jobs_table: str = os.getenv("BATCH_JOBS_TABLE", "transcription-batch-jobs")
    reviewer_function_name: str = os.getenv("REVIEWER_FUNCTION_NAME", "")
```

### `.env.jinja` additions

```
# Gemini webhook (uncomment when using GeminiPostProcessing)
# GOOGLE_WEBHOOK_AUDIENCE={{ gemini.webhook_audience }}
# GOOGLE_JWKS_URL=https://generativelanguage.googleapis.com/.well-known/jwks.json
# BATCH_JOBS_TABLE={{ dynamodb.batch_jobs_table }}
# REVIEWER_FUNCTION_NAME={{ lambda.reviewer_function_name }}
```

### `pyproject.toml` additions

```toml
dependencies = [
    "boto3>=1.35.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0.0",
    "dependency-injector>=4.41.0",
    "PyJWT>=2.8.0",
    "cryptography>=41.0.0",
    "requests>=2.31.0",
]
```

---

## File Changes Summary

### New Files

| File | Description |
|------|-------------|
| `models/post_processing.py` | `PostProcessing` ABC + `AuthenticationError` exception |
| `handlers/bedrock_post_processing.py` | `BedrockPostProcessing` — wraps existing Bedrock flow |
| `handlers/gemini_post_processing.py` | `GeminiPostProcessing` — webhook verify + DynamoDB + Lambda invoke |
| `services/jwt_verifier.py` | `JWTVerifier` — RS256 verification using Google JWKS |
| `infrastructure/dynamodb_client.py` | `DynamoDBClient` — DynamoDB operations wrapper |

### Modified Files

| File | Change |
|------|--------|
| `handler.py` | Use `PostProcessing` ABC from DI container |
| `config.py` | Add `google_webhook_kid`, `google_jwks_url`, `batch_jobs_table`, `reviewer_function_name` |
| `.env.jinja` | Add Gemini webhook env vars (commented out) |
| `infrastructure/dependency_injection.py` | Add `post_processor` provider with both impls (one commented) |
| `pyproject.toml` | Add `PyJWT`, `cryptography` dependencies |

### Unchanged Files

| File | Reason |
|------|--------|
| `handlers/process.py` | Used by `BedrockPostProcessing`, unchanged |
| `services/batch_result_processor.py` | Used by Bedrock flow, unchanged |
| `utils/vtt_converter.py` | Shared utility, unchanged |
| `infrastructure/s3_client.py` | Used by Bedrock flow, unchanged |
| `infrastructure/sqs_client.py` | Used by Bedrock flow, unchanged |
| `models/schemas.py` | Bedrock models, unchanged |

---

## Directory Structure (Final)

```
post_inference/src/post_inference/
├── __init__.py
├── config.py                              # + Gemini webhook config
├── handler.py                             # refactored: PostProcessing ABC
├── handlers/
│   ├── __init__.py
│   ├── process.py                         # unchanged
│   ├── bedrock_post_processing.py         # NEW
│   └── gemini_post_processing.py          # NEW
├── infrastructure/
│   ├── __init__.py
│   ├── dependency_injection.py            # + post_processor provider
│   ├── dynamodb_client.py                 # NEW
│   ├── s3_client.py
│   └── sqs_client.py
├── models/
│   ├── __init__.py
│   ├── post_processing.py                 # NEW: ABC
│   └── schemas.py
├── services/
│   ├── __init__.py
│   ├── batch_result_processor.py
│   └── jwt_verifier.py                    # NEW
└── utils/
    ├── __init__.py
    └── vtt_converter.py
```

---

## Migration Path

### Phase 1: Abstract Class + Bedrock Refactor
1. Create `PostProcessing` ABC in `models/post_processing.py`
2. Create `BedrockPostProcessing` wrapping existing `process_batch_output()`
3. Update `handler.py` to use ABC from DI
4. Update DI container with `post_processor` provider
5. **Zero behavior change** — Bedrock flow works exactly as before

### Phase 2: DynamoDB Client
1. Create `infrastructure/dynamodb_client.py` with `get_item()` and `update_item()`
2. Wire in DI container

### Phase 3: JWT Verifier
1. Create `services/jwt_verifier.py`
2. Add `PyJWT`, `cryptography` to `pyproject.toml`
3. Wire in DI container

### Phase 4: GeminiPostProcessing
1. Create `handlers/gemini_post_processing.py`
2. Add config fields + env vars
3. Wire in DI container (commented out)

### Phase 5: Activation
1. Uncomment `GeminiPostProcessing` in DI, comment `BedrockPostProcessing`
2. Deploy with API Gateway trigger
3. Set `GEMINI_BATCH_WEBHOOK_URL` in transcription_reviewer to API Gateway endpoint

---

## Design Decisions

- **Single Lambda, two implementations.** DI container selects active provider. Avoids separate deployment for a lightweight handler.
- **DynamoDBClient wrapper.** No raw boto3 in handlers. Consistent with `transcription_reviewer` patterns.
- **Status "finished" immediately.** Idempotency gate — second webhook for same batch_job_id sees "finished" and returns 200 without re-invoking reviewer.
- **Always return 200.** Gemini retries on non-2xx. Unknown jobs, already-processed jobs, non-success events all return 200 to prevent retry storms.
- **JWKS cached at class level.** Survives across warm Lambda invocations. Avoids HTTP call on every webhook.
- **Audience from env var.** Expected audience configured via `GOOGLE_WEBHOOK_AUDIENCE`. Kid is extracted dynamically from JWT header and matched against JWKS. Audience claim verified by PyJWT during decode.
