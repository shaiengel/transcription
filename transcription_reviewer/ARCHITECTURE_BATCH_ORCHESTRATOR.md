# Architecture: Orchestrators for Transcription Review

## Problem

`process_transcriptions()` in `handlers/review.py` is coupled to per-file synchronous processing. Each file goes through `prepare_data → invoke → post_process` individually. This works for on-demand Gemini calls but is inefficient and structurally incompatible with batch APIs (Gemini Batch) where you want to:

1. Collect all work upfront
2. Submit one batch request
3. Receive results asynchronously (webhook)
4. Potentially iterate (retry/reasoning) at batch level
5. Finalize all files at once (S3 + SQS)

## Solution: Single-Layer Orchestrator Architecture

Each orchestrator is self-contained — owns its entire lifecycle in a single `start()` method. No separate pipeline classes for batch. Shared Gemini API + DynamoDB operations extracted to a composition helper (`GeminiBatchService`).

```
ReviewOrchestrator (abstract)
  ├── OnDemandOrchestrator
  │     uses: GeminiPipeline (existing LLMPipeline)
  │
  ├── GeminiBatchOrchestrator
  │     uses: GeminiBatchService (shared helper)
  │
  └── GeminiBatchRetriggerOrchestrator
        uses: GeminiBatchService (shared helper)

GeminiBatchService (composition — not an orchestrator, stateless)
  Encapsulates: Gemini client, JSONL build/upload/submit,
                result fetch/parse, GCS cleanup,
                FIX_TRACKER_TABLE + BATCH_JOBS_TABLE operations
  Cache dict owned by orchestrators, passed to service methods
```

---

## Orchestrator API

All orchestrators use `config.py` for S3 buckets, DynamoDB table, and other environment params.

```python
class ReviewOrchestrator(ABC):

    @abstractmethod
    def start(self) -> StartResult:
        """Execute the full processing lifecycle. Returns immediately for batch (async)."""
        pass
```

### OnDemandOrchestrator

```python
class OnDemandOrchestrator(ReviewOrchestrator):
    """Per-file synchronous processing. Absorbs the process_transcriptions loop."""

    def __init__(
        self,
        s3_reader: S3Reader,
        pipeline: LLMPipeline,  # GeminiPipeline
        transcription_fixer: TranscriptionFixer,
        dynamo_reader: DynamoReader,
    ): ...

    def start(self) -> StartResult:
        # Full lifecycle:
        # 1. List .txt files from S3 (transcription_bucket)
        # 2. For each file:
        #    a. DynamoDB gate — skip if status != "transcribed"
        #    b. Fetch content from S3
        #    c. Fetch system prompt
        #    d. Build TranscriptionFile
        #    e. pipeline.prepare_data([file], context)
        #    f. pipeline.invoke(prepared, context)
        #    g. pipeline.post_process(response, prepared)
        #    h. Update DynamoDB status to "fixed" if successful
        #    i. Timeout check — if Lambda running low, break and set timed_out
        # 3. Return StartResult with counts
        ...
```

### GeminiBatchOrchestrator

```python
class GeminiBatchOrchestrator(ReviewOrchestrator):
    """Initial trigger: collect files → build JSONL → submit Gemini batch."""

    def __init__(
        self,
        s3_reader: S3Reader,
        s3_client: S3Client,
        dynamo_reader: DynamoReader,
        transcription_fixer: TranscriptionFixer,
        batch_service: GeminiBatchService,
    ): ...

    def start(self) -> StartResult:
        # 1. List .txt files from S3
        # 2. DynamoDB gate (status == "transcribed")
        # 3. Fetch system prompts
        # 4. Split files > split_by_words_max into chunks → BatchEntry list
        # 5. Store original chunks to TEMPORARY_FIX_BUCKET/{record_id}.original.txt
        # 6. Register each entry in FIX_TRACKER_TABLE
        # 7. Create caches per unique system_prompt (if GEMINI_CACHE_ENABLED)
        # 8. Build JSONL from entries
        # 9. Upload JSONL to GCS via Gemini Files API
        # 10. Submit batch to Gemini Batch API → get batch_job_id
        # 11. Store in BATCH_JOBS_TABLE
        # Return StartResult(mode="async", job_ref=batch_job_name)
        ...
```

### GeminiBatchRetriggerOrchestrator

```python
class GeminiBatchRetriggerOrchestrator(ReviewOrchestrator):
    """Result trigger: fetch batch results → diff eval → retry or finalize."""

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        dynamo_reader: DynamoReader,
        batch_service: GeminiBatchService,
        batch_job_id: str,  # passed at construction
    ): ...

    def start(self) -> StartResult:
        # --- Retrieval ---
        # 1. Look up batch_job_id in BATCH_JOBS_TABLE → gcs_input_file, output_file_uri, caches
        # 2. Download and parse output file (output_file_uri) from GCS
        # 3. Parse results — each entry has key, response (GenerateContentResponse), error
        # 4. Clean up: delete GCS input file (via Files API), delete BATCH_JOBS row
        #
        # --- Evaluation (per result entry) ---
        # 5. Get tracker from FIX_TRACKER_TABLE
        # 6. Check for errors first:
        #    ├── ERROR (no response_text, or "error" in result):
        #    │     - Dead-letter the entire media (all chunks of that stem):
        #    │       a. Copy original media files ({stem}.*) to DEAD_LETTER_BUCKET
        #    │       b. Copy any existing fixed chunks ({stem}*.txt) from TEMPORARY_FIX_BUCKET to DEAD_LETTER_BUCKET
        #    │       c. Delete all tracker entries for that stem from FIX_TRACKER_TABLE
        #    │       d. Delete temp files for that stem from TEMPORARY_FIX_BUCKET
        #    │       e. Mark stem as failed, skip all remaining chunks of that stem
        #    │
        #    Then calculate new_diff = word_count_diff(response_text, original_word_count)
        #    ├── new_diff <= max_word_diff (GOOD):
        #    │     Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt
        #    │     Update tracker: completed=true, diff_value=new_diff
        #    │
        #    ├── new_diff < stored diff_value (BETTER but not good enough):
        #    │     Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt
        #    │     Update tracker: diff_value=new_diff, retry_number++
        #    │     Add to retry accumulator (with ORIGINAL chunk)
        #    │     If last retry → use reasoning prompt
        #    │
        #    └── new_diff >= stored diff_value (WORSE or equal):
        #          Keep existing in TEMPORARY_FIX_BUCKET
        #          Update tracker: retry_number++
        #          Add to retry accumulator (with ORIGINAL chunk)
        #          If last retry → use reasoning prompt
        #
        # --- Retry or Finalize ---
        # 7. If retry entries accumulated:
        #    a. Create caches per unique system_prompt (if GEMINI_CACHE_ENABLED)
        #    b. Build JSONL from retry entries
        #    c. Upload + submit new batch
        #    d. Store new batch_job_id in BATCH_JOBS_TABLE
        #    Return StartResult(mode="async", job_ref=new_batch_job_name)
        #
        # 8. If no retry entries (all chunks done):
        #    a. Merge split chunks: read {stem}_{1..N}.txt → concatenate → {stem}.txt
        #    b. Upload merged {stem}.txt to dynamo_entry.media_fixed_transcribed_bucket
        #    c. Copy .time → .pre-fix.time
        #    d. Send SQS messages for all completed media_ids
        #    e. Update MEDIA_TABLE status to "fixed"
        #    f. Clean up: source files, temporary-fix-files, FIX_TRACKER_TABLE entries
        #    Return StartResult(mode="completed", fixed=N, failed=M)
        ...
```

---

## GeminiBatchService (Shared Helper)

Composition helper used by both batch orchestrators. Encapsulates Gemini API client, JSONL operations, and DynamoDB table operations. Not an orchestrator — no lifecycle methods. Stateless with respect to caches: orchestrators own the `caches: dict[str, tuple[str, float]]` dict (keyed by `prompt_hash`) and pass it to service methods.

```python
class GeminiBatchService:
    """Gemini Batch API operations + DynamoDB table ops for batch orchestrators.
    Stateless — does not hold cache state. Callers own the caches dict."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self._client = genai.Client(api_key=config.google_api_key)
        self._dynamodb_client = dynamodb_client

    # --- Gemini API ---

    @staticmethod
    def compute_prompt_hash(system_prompt: str) -> str:
        """md5[:8] of whitespace-normalized prompt. Used as cache dict key and BatchEntry.prompt_hash."""
        ...

    def get_or_create_cache(
        self, system_prompt: str, caches: dict[str, tuple[str, float]]
    ) -> str | None:
        """Get or create a cached content entry for the system prompt.
        Mutates `caches` dict (keyed by prompt_hash) with (cache_name, expiry).
        Returns cache name or None if caching disabled/failed."""
        ...

    def build_and_submit_batch(
        self,
        entries: list[BatchEntry],
        caches: dict[str, tuple[str, float]],
        reasoning_media_ids: set[str] | None = None,
    ) -> tuple[str, str]:
        """Build JSONL, upload to GCS, submit batch. Returns (job_name, gcs_input_file).
        Uses entry.prompt_hash to look up cache_name from caches dict.
        If GEMINI_BATCH_WEBHOOK_URL configured, sets webhook_config on batch."""
        ...

    def fetch_batch_results(self, output_file_uri: str) -> list[dict]:
        """Download output JSONL from GCS via client.files.download(),
        parse each line into {key, response, error} dicts."""
        ...

    def delete_gcs_file(self, gcs_input_file: str) -> None:
        """Delete input JSONL from GCS via Files API."""
        ...

    # --- JSONL ---

    def _build_jsonl(
        entries: list[BatchEntry],
        caches: dict[str, tuple[str, float]],
        reasoning_media_ids: set[str] | None = None,
    ) -> str:
        """Build JSONL string. Looks up cache via entry.prompt_hash in caches dict."""
        ...

    @staticmethod
    def split_by_words(content: str, max_words: int) -> list[str]: ...

    # --- FIX_TRACKER_TABLE ---

    def register_fix_tracker_entry(
        self, record_id: str, stem: str, original_word_count: int, total_chunks: int
    ) -> None: ...

    def get_fix_tracker(self, record_id: str) -> dict | None: ...

    def update_fix_tracker(
        self, record_id: str,
        completed: bool | None = None,
        diff_value: int | None = None,
        retry_number: int | None = None,
    ) -> None: ...

    def delete_fix_tracker(self, record_id: str) -> None: ...

    def collect_completed_stems(self) -> dict[str, list[str]]:
        """Scan tracker for all completed entries, grouped by stem."""
        ...

    # --- BATCH_JOBS_TABLE ---

    def store_batch_job(
        self, batch_job_id: str, gcs_input_file: str,
        caches: dict[str, tuple[str, float]],
    ) -> None:
        """Store batch job + serialized caches dict in BATCH_JOBS_TABLE."""
        ...

    def get_batch_job(self, batch_job_id: str) -> tuple[dict | None, dict[str, tuple[str, float]]]:
        """Returns (job_record, deserialized caches dict)."""
        ...

    def delete_batch_job(self, batch_job_id: str) -> None: ...

    # --- Cache Cleanup ---

    def delete_prompt_caches(self, caches: dict[str, tuple[str, float]]) -> None:
        """Delete all cached content via client.caches.delete(). Called only at finalization."""
        ...

    # --- Dead Letter ---

    def dead_letter_media(
        self, stem: str, record_ids: list[str],
        transcription_bucket: str, temp_fix_bucket: str, dead_letter_bucket: str,
    ) -> None:
        """Copy original media + fixed chunks to dead-letter bucket, clean up tracker + temp files."""
        ...
```

### System Prompt Caching in Batch

Gemini Batch API supports `cached_content` in JSONL entries ([docs](https://ai.google.dev/gemini-api/docs/files)).

**Rules:**
- If `GEMINI_CACHE_ENABLED=true` (config.cache_enabled): create cached content per unique system prompt before building JSONL. Set `cached_content` field in each JSONL entry.
- If `GEMINI_CACHE_ENABLED=false`: set `system_instruction` inline per entry (no caching).
- **Reasoning entries are never cached.** When a chunk is on its last retry, it uses `{stem}.template.reasoning.txt` — a different system prompt. These entries use `system_instruction` inline, not `cached_content`.
- **Cache dict ownership:** Orchestrators own `caches: dict[str, tuple[str, float]]` (keyed by `prompt_hash` = md5[:8] of normalized prompt). `GeminiBatchService.get_or_create_cache()` mutates this dict. Dict is persisted in `BATCH_JOBS_TABLE` so retrigger orchestrator can restore it across Lambda invocations.
- **Cache cleanup:** `delete_prompt_caches(caches)` called only by retrigger orchestrator at finalization (no more retries).

**JSONL entry format (cached):**
```json
{
  "key": "151415_1",
  "request": {
    "cached_content": "cachedContents/abc123",
    "contents": [{"role": "user", "parts": [{"text": "...transcription chunk..."}]}],
    "generation_config": {"temperature": 0.1, "top_p": 0.1, "top_k": 1, "max_output_tokens": 60000}
  }
}
```

**JSONL entry format (uncached — reasoning or cache disabled):**
```json
{
  "key": "151415_1",
  "request": {
    "system_instruction": {"parts": [{"text": "...reasoning system prompt..."}]},
    "contents": [{"role": "user", "parts": [{"text": "...transcription chunk..."}]}],
    "generation_config": {"temperature": 0.1, "top_p": 0.1, "top_k": 1, "max_output_tokens": 60000}
  }
}
```

### Diff Strategy (Quality Check)

Word-count diff logic in `utils/word_diff.py` (shared by both on-demand `GeminiPipeline` and batch orchestrators):

```python
# utils/word_diff.py
def word_count_diff(text: str, original_word_count: int) -> int:
    return abs(len(text.split()) - original_word_count)
```

- `original_word_count` stored in FIX_TRACKER_TABLE at initial trigger time
- `max_word_diff` threshold (default 100) from config determines "good enough"
- JSONL always sends the **original chunk** (never a previous LLM result) — goal is best single-pass fix
- Each result trigger compares `new_diff` against stored `diff_value` to decide keep/replace
- Best result per chunk stored at `TEMPORARY_FIX_BUCKET/{stem}_{chunk_idx}.txt` (split) or `TEMPORARY_FIX_BUCKET/{stem}.txt` (unsplit)

---

## Webhook / Results Handler

Two-Lambda design. Webhook Lambda must return fast to Gemini. Processing happens in existing reviewer Lambda.

Webhook URL is configured via `GEMINI_BATCH_WEBHOOK_URL` env var and passed to Gemini Batch API using `webhook_config` in `CreateBatchJobConfig`. Gemini calls this URL when batch job completes.

**NOTE**: Do not implement the lightweight webhook Lambda now. It will be implemented later as a separate file (`webhook_handler.py`).

### File Concepts: Input vs Output

Two distinct GCS files exist in the batch flow — do not confuse them:

| Concept | Field in BATCH_JOBS_TABLE | Origin | Cleanup |
|---------|--------------------------|--------|---------|
| **Input JSONL** | `gcs_input_file` | Created locally, uploaded via `client.files.upload()`. Contains request entries sent TO Gemini. | Deleted by retrigger orchestrator via `client.files.delete()` after processing. |
| **Output result file** | `output_file_uri` | Created by Gemini when batch completes. GCS URI delivered via webhook envelope `data.output_file_uri`. | Managed by Gemini's lifecycle — not deleted by our code. |

Results are fetched by downloading `output_file_uri` via `client.files.download()` and parsing the JSONL. Each line contains `{key, response, error}`.

### Webhook Envelope Format

When a batch job completes, Gemini sends a POST to the registered webhook URL:

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

Other event types: `batch.failed` (has `error_code`, `error_message`), `batch.cancelled`, `batch.expired`.

### Webhook Signature Verification

Gemini webhook uses JWT in the `Webhook-Signature` header, signed with RS256:

1. Extract JWT from `Webhook-Signature` header
2. Fetch Google's public keys from `https://generativelanguage.googleapis.com/.well-known/jwks.json`
3. Verify JWT using RS256 with matching `kid` (key ID)
4. Validate audience claim

Use `PyJWT` + `cryptography` for verification. Cache JWKS at module level for Lambda container reuse.

### Architecture

```
Gemini Batch Complete (webhook callback → GEMINI_BATCH_WEBHOOK_URL)
         ↓
    Webhook Lambda (NEW, lightweight, separate file — TO BE IMPLEMENTED LATER)
       - Verify JWT signature from Webhook-Signature header
       - Parse webhook envelope JSON body
       - Extract data.id (batch_job_id) and data.output_file_uri
       - If type != "batch.succeeded" → return 200 (ignore other events)
       - Look up batch_job_id in BATCH_JOBS_TABLE
       - If not found or status == "received" → return 200 (idempotent)
       - Update BATCH_JOBS_TABLE: output_file_uri = data.output_file_uri, status = "received"
       - Invoke transcription-reviewer Lambda async with { "batch_job_id": "..." }
       - Return 200 immediately
         ↓
    Transcription Reviewer Lambda (EXISTING, new invocation mode)
       - Detect invocation type:
         ├── No event payload / CloudWatch alarm → INITIAL TRIGGER (normal)
         └── event has "batch_job_id"            → RESULT TRIGGER
```

### Webhook Registration

`GeminiBatchService.build_and_submit_batch()` passes `webhook_config` when `GEMINI_BATCH_WEBHOOK_URL` is set:

```python
batch_config = types.CreateBatchJobConfig(
    display_name=f"transcription_batch_{int(time.time())}",
)
if self._webhook_url:
    batch_config.webhook_config = types.WebhookConfig(
        uris=[self._webhook_url],
    )
batch = self._client.batches.create(
    model=self._model_name,
    src=gcs_input_file,
    config=batch_config,
)
```

---

## Modified Lambda Handler

```python
# handler.py — updated

def lambda_handler(event, context):
    container = DependenciesContainer()
    batch_job_id = event.get("batch_job_id") if isinstance(event, dict) else None

    if batch_job_id:
        # RESULT TRIGGER — use retrigger orchestrator
        orchestrator = container.gemini_batch_retrigger_orchestrator(batch_job_id)
    else:
        # INITIAL TRIGGER — config selects on-demand vs batch
        orchestrator = container.orchestrator()

    result = orchestrator.start()

    if result.timed_out:
        _reinvoke_self(context, event)

    return {"statusCode": 200, "result": result.to_dict()}
```

### Webhook Lambda (NEW — TO BE IMPLEMENTED LATER)

> **NOTE**: This Lambda will be implemented in a future phase as a **separate file** (`webhook_handler.py`).
> It is intentionally lightweight — does NOT import `GeminiBatchService` or the Gemini SDK.
> Uses raw boto3 (DynamoDB + Lambda invoke) only.

```python
# webhook_handler.py — lightweight, separate Lambda, must return fast to Gemini
# Env vars: BATCH_JOBS_TABLE, REVIEWER_FUNCTION_ARN, GEMINI_WEBHOOK_JWKS_URL (optional)
# Dependencies: PyJWT, cryptography, boto3, requests (or urllib3)

import json, os, jwt, requests, boto3
from functools import lru_cache

JWKS_URL = os.getenv(
    "GEMINI_WEBHOOK_JWKS_URL",
    "https://generativelanguage.googleapis.com/.well-known/jwks.json",
)

@lru_cache(maxsize=1)
def _get_jwks():
    return requests.get(JWKS_URL).json()

def _verify_signature(token: str) -> dict:
    """Verify JWT from Webhook-Signature header against Google's JWKS."""
    jwks = _get_jwks()
    header = jwt.get_unverified_header(token)
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return jwt.decode(token, public_key, algorithms=["RS256"])

def lambda_handler(event, context):
    # 1. Verify webhook signature
    signature = event["headers"].get("Webhook-Signature", "")
    try:
        _verify_signature(signature)
    except Exception:
        return {"statusCode": 401, "body": "Invalid signature"}

    # 2. Parse webhook envelope
    body = json.loads(event.get("body", "{}"))
    event_type = body.get("type", "")
    data = body.get("data", {})
    batch_job_id = data.get("id", "")
    output_file_uri = data.get("output_file_uri", "")

    if event_type != "batch.succeeded" or not batch_job_id:
        return {"statusCode": 200}

    # 3. Idempotency check
    dynamo = boto3.client("dynamodb")
    table = os.environ["BATCH_JOBS_TABLE"]
    job = dynamo.get_item(TableName=table, Key={"batch_job_id": {"S": batch_job_id}})
    item = job.get("Item")
    if not item or item.get("status", {}).get("S") == "received":
        return {"statusCode": 200}

    # 4. Update BATCH_JOBS_TABLE: store output_file_uri, set status="received"
    dynamo.update_item(
        TableName=table,
        Key={"batch_job_id": {"S": batch_job_id}},
        UpdateExpression="SET output_file_uri = :o, #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":o": {"S": output_file_uri},
            ":s": {"S": "received"},
        },
    )

    # 5. Invoke reviewer Lambda async with batch_job_id
    boto3.client("lambda").invoke(
        FunctionName=os.environ["REVIEWER_FUNCTION_ARN"],
        InvocationType="Event",
        Payload=json.dumps({"batch_job_id": batch_job_id}),
    )

    return {"statusCode": 200}
```

---

## Initial Trigger Flow (GeminiBatchOrchestrator)

```
GeminiBatchOrchestrator.start():
  1. List .txt files from S3
  2. DynamoDB gate on MEDIA_TABLE (status == "transcribed")
  3. Fetch system prompts
  4. Compute prompt_hash = GeminiBatchService.compute_prompt_hash(system_prompt)
  5. Split file > 5000 words into chunks → BatchEntry list (each entry carries prompt_hash)
  6. Store original chunk to TEMPORARY_FIX_BUCKET/{record_id}.original.txt
  7. Register each entry in FIX_TRACKER_TABLE:
     record_id (PK), retry_number=1, completed=false,
     diff_value=999999, original_word_count=len(chunk.split())
  8. Init caches: dict[str, tuple[str, float]] = {}
  9. Create caches per unique system_prompt via svc.get_or_create_cache(prompt, caches)
  10. Build JSONL from entries (uses entry.prompt_hash to look up cache in caches dict)
  11. Upload JSONL to GCS via Gemini Files API
  12. Submit batch to Gemini Batch API (with webhook_config if GEMINI_BATCH_WEBHOOK_URL set) → get batch_job_id
  13. Store in BATCH_JOBS_TABLE via svc.store_batch_job(batch_job_id, gcs_input_file, caches):
      batch_job_id (PK), gcs_input_file, prompt_caches, output_file_uri=null, status="submitted"
  Return StartResult(mode="async", job_ref=batch_job_name)
```

## Result Trigger Flow (GeminiBatchRetriggerOrchestrator)

```
Reviewer Lambda receives { "batch_job_id": "..." }
         ↓
    GeminiBatchRetriggerOrchestrator(batch_job_id) created via DI
         ↓
    start():

      --- Retrieval Phase ---
      1. Look up batch_job_id in BATCH_JOBS_TABLE → gcs_input_file, output_file_uri, caches
         (caches: dict[str, tuple[str, float]] deserialized from prompt_caches)
      2. Download output file from GCS via client.files.download(file=output_file_uri)
      3. Parse output JSONL → list of {key, response, error} entries
      4. Delete GCS input file (via Gemini Files API)
      6. Delete batch_job_id row from BATCH_JOBS_TABLE

      --- Error Handling Phase (per result entry) ---
      7. Check batchStats.failedRequestCount from job response
      8. For each result entry, check if response is error (status object, not
         GenerateContentResponse):
         │
         └── ERROR:
               - Identify stem from record_id (strip chunk suffix)
               - Copy original media from TRANSCRIPTION_BUCKET/{stem}.txt
                 to DEAD_LETTER_BUCKET/{stem}.txt
               - Copy all fixed chunks for that stem from
                 TEMPORARY_FIX_BUCKET/{stem}*.txt to DEAD_LETTER_BUCKET/
               - Delete all FIX_TRACKER_TABLE entries for that stem
               - Delete temp files for that stem from TEMPORARY_FIX_BUCKET
               - Mark stem as dead-lettered (skip in evaluation + finalize)
               - Log error details for observability

      --- Evaluation Phase (per non-error result entry) ---
      9. Get tracker from FIX_TRACKER_TABLE
      10. Calculate new_diff = word_count_diff(response_text, original_word_count)
         │
         ├── new_diff <= max_word_diff (GOOD):
         │     - Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt
         │     - Update FIX_TRACKER_TABLE: completed=true, diff_value=new_diff
         │
         ├── new_diff < stored diff_value (BETTER but not good enough):
         │     - Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt (replace previous)
         │     - Update FIX_TRACKER_TABLE: diff_value=new_diff, retry_number++
         │     - Add to retry accumulator (with ORIGINAL chunk from
         │       TEMPORARY_FIX_BUCKET/{record_id}.original.txt)
         │     - If last retry → use reasoning prompt for this chunk
         │
         └── new_diff >= stored diff_value (WORSE or equal):
               - Keep existing file in TEMPORARY_FIX_BUCKET unchanged
               - Update FIX_TRACKER_TABLE: retry_number++ (diff_value stays)
               - Add to retry accumulator (with ORIGINAL chunk)
               - If last retry → use reasoning prompt for this chunk

      --- Retry or Finalize ---
      11. If retry entries accumulated:
           a. Build retry BatchEntry list (each carries prompt_hash via compute_prompt_hash)
           b. Create/refresh caches per unique prompt_hash via svc.get_or_create_cache(prompt, caches)
           c. Build JSONL from retry entries (uses entry.prompt_hash for cache lookup)
           d. Upload to GCS via Files API
           e. Submit new batch to Gemini → new batch_job_id
           f. Store new batch_job_id + caches via svc.store_batch_job(id, gcs_input_file, caches)
           Return StartResult(mode="async", job_ref=new_batch_job_name)

      12. If no retry entries (all chunks done):
           a. Merge split chunks: read {stem}_{1..N}.txt → concatenate → {stem}.txt
           b. Upload merged {stem}.txt to dynamo_entry.media_fixed_transcribed_bucket
           c. Copy .time → .pre-fix.time
           d. Send SQS messages for all completed media_ids
           e. Update MEDIA_TABLE status to "fixed"
           f. Clean up: source files, temporary-fix-files, FIX_TRACKER_TABLE entries
           g. Delete cached content via svc.delete_prompt_caches(caches)
           Return StartResult(mode="completed", fixed=N, failed=M)
```

---

## DynamoDB Tables

Three tables involved in batch processing:

#### 1. MEDIA_TABLE (existing)

Already used by `TranscriptionFile` / `DynamoReader`. No schema changes.

| Field | Used for |
|-------|----------|
| `media_id` (PK) | Gate: only process if status == `"transcribed"` |
| `status` | Updated to `"fixed"` on completion |
| `context_files_bucket_s3` | System prompt + reasoning prompt location |
| `media_transcribed_bucket` | Source bucket for `.time` file |
| `media_fixed_transcribed_bucket` | Destination for merged fixed `.txt` |

#### 2. FIX_TRACKER_TABLE (existing, reused for batch)

Same table used by on-demand `FixTrackerService`. In batch mode, tracks per-chunk retry state.

| Field | Type | Description |
|-------|------|-------------|
| `media_id` | S (PK) | `{stem}` or `{stem}_{chunk_idx}` if split |
| `retry_number` | N | Current retry (starts at 1) |
| `completed` | BOOL | Whether chunk passed quality check |
| `diff_value` | N | Best word-count diff so far (initialized to 999999) |
| `original_word_count` | N | Word count of original chunk (for diff calculation) |

#### 3. BATCH_JOBS_TABLE (NEW)

Tracks active batch jobs. Row deleted after result processing.

| Field | Type | Description |
|-------|------|-------------|
| `batch_job_id` | S (PK) | Gemini batch job identifier |
| `gcs_input_file` | S | Input JSONL file name uploaded via Files API (deleted by retrigger after processing) |
| `output_file_uri` | S or NULL | GCS URI of output result file (set by webhook Lambda, used for debugging only — results fetched via SDK) |
| `status` | S | `submitted` → `received` (row deleted after processing) |
| `prompt_caches` | M | Serialized cache dict: `{prompt_hash: {"name": cache_name, "expiry": timestamp}}` |

---

## File Changes Summary

### New Files

| File | Description |
|------|-------------|
| `models/review_orchestrator.py` | `ReviewOrchestrator` abstract class (start only) + `StartResult` |
| `handlers/on_demand_orchestrator.py` | `OnDemandOrchestrator` — absorbs process_transcriptions loop |
| `handlers/gemini_batch_orchestrator.py` | `GeminiBatchOrchestrator` — initial trigger, self-contained |
| `handlers/gemini_batch_retrigger_orchestrator.py` | `GeminiBatchRetriggerOrchestrator` — result trigger, self-contained |
| `services/gemini_batch_service.py` | `GeminiBatchService` — shared Gemini API + DynamoDB helper |
| `utils/word_diff.py` | Shared word-count diff function |

### Modified Files

| File | Change |
|------|--------|
| `handler.py` | Detect invocation type (initial vs result trigger), route to correct orchestrator |
| `infrastructure/dependency_injection.py` | Add `GeminiBatchService`, `GeminiBatchOrchestrator`, `GeminiBatchRetriggerOrchestrator` wiring |
| `config.py` | Add batch_jobs_table name, results bucket |

### Deleted Files

| File | Reason |
|------|--------|
| `handlers/review.py` | `process_transcriptions()` absorbed into `OnDemandOrchestrator.start()` |
| `handlers/batch_orchestrator.py` | Split into two orchestrators |
| `services/gemini_batch_pipeline.py` | Absorbed into `GeminiBatchService` + orchestrators |

### Unchanged Files

| File | Reason |
|------|--------|
| `services/gemini_pipeline.py` | On-demand pipeline, used by `OnDemandOrchestrator` |
| `models/llm_pipeline.py` | Abstract stays, `GeminiPipeline` extends it |
| `services/bedrock_batch_pipeline.py` | Separate backend, unaffected |

---

## Migration Path

### Phase 1: Orchestrator Scaffold + OnDemandOrchestrator
1. Create `ReviewOrchestrator` abstract class with `start()` only
2. Implement `OnDemandOrchestrator` absorbing `process_transcriptions()` loop
3. Update `handler.py` to use orchestrator
4. **Zero behavior change** — on-demand path works exactly as before

### Phase 2: GeminiBatchService
1. Create `services/gemini_batch_service.py`
2. Extract shared code: Gemini caching, JSONL build/upload/submit, result fetch/parse, DynamoDB table ops
3. Wire in DI container

### Phase 3: GeminiBatchOrchestrator (Initial Trigger)
1. Create `handlers/gemini_batch_orchestrator.py`
2. `start()` does: file listing → splitting → tracker registration → batch submission
3. Uses `GeminiBatchService` for Gemini API + DynamoDB
4. Wire in DI, add config flag selection

### Phase 4: GeminiBatchRetriggerOrchestrator (Result Trigger)
1. Create `handlers/gemini_batch_retrigger_orchestrator.py`
2. `start()` does: result fetch → diff eval → retry submit or finalize
3. Update `handler.py` to route `batch_job_id` events to retrigger orchestrator
4. Wire in DI

### Phase 5: Cleanup
1. Delete `handlers/review.py`
2. Delete `handlers/batch_orchestrator.py`
3. Delete `services/gemini_batch_pipeline.py`
4. Remove old factories from DI container

### Phase 6: Webhook Lambda (LATER)
1. Implement `webhook_handler.py` (lightweight Lambda)
2. Deploy webhook Lambda + API Gateway endpoint
3. Set `GEMINI_BATCH_WEBHOOK_URL` to API Gateway endpoint URL
4. Webhook registered automatically via `webhook_config` in batch submission

---

## Design Decisions

- **No two-layer abstraction.** Each orchestrator owns its full lifecycle — no separate pipeline classes for batch.
- **GeminiBatchService for shared code.** Composition over inheritance. Both batch orchestrators use it, but each controls when/how to call its methods.
- **batch_job_id as constructor param.** `GeminiBatchRetriggerOrchestrator` is constructed with the specific job to process. Single-purpose, testable.
- **Dead-letter on Gemini errors.** When Gemini batch returns errors (status object instead of GenerateContentResponse), the entire media (all chunks of that stem) is copied to `DEAD_LETTER_BUCKET` along with any fixed chunks. Tracker entries and temp files for that stem are cleaned up. Non-error chunks of other stems proceed normally.
- **Reasoning prompt on last retry.** When a chunk reaches its last retry, use `{stem}.template.reasoning.txt` from `context_files_bucket_s3`.
- **Chunk merge on finalize.** Split chunks (`{stem}_{1..N}.txt`) concatenated in order → single `{stem}.txt` → uploaded.
- **Cleanup per result trigger.** Each invocation deletes: BATCH_JOBS row, GCS input file (via Files API). Output result file managed by Gemini's lifecycle.
- **JSONL always sends original chunk.** Never a previous LLM result. Goal is best single-pass fix.

## Open Questions

None — all resolved.
