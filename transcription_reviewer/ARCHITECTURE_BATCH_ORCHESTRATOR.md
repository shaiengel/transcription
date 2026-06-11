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

GeminiBatchService (composition — not an orchestrator)
  Encapsulates: Gemini client, caching, JSONL build/upload/submit,
                result fetch/parse, GCS cleanup,
                FIX_TRACKER_TABLE + BATCH_JOBS_TABLE operations
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
        # 1. Look up batch_job_id in BATCH_JOBS_TABLE → result_file_s3, gcs_file_name
        # 2. Fetch results: from S3 (if result_file_s3 set) or Gemini API
        #    - Check batchStats.failedRequestCount for batch-level failures
        # 3. Parse result JSONL — each line is either GenerateContentResponse or error status
        # 4. Clean up: delete old GCS input file, delete result file from S3, delete BATCH_JOBS row
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

Composition helper used by both batch orchestrators. Encapsulates Gemini API client, caching, JSONL operations, and DynamoDB table operations. Not an orchestrator — no lifecycle methods.

```python
class GeminiBatchService:
    """Gemini Batch API operations + DynamoDB table ops for batch orchestrators."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self._client = genai.Client(api_key=config.google_api_key)
        self._dynamodb_client = dynamodb_client
        self._prompt_caches: dict[str, tuple[str, float]] = {}

    # --- Gemini API ---

    def get_or_create_cache(self, system_prompt: str) -> str | None:
        """Get or create a cached content entry for the system prompt."""
        ...

    def build_and_submit_batch(
        self,
        entries: list[BatchEntry],
        reasoning_prompts: set[str] | None = None,
    ) -> tuple[str, str]:
        """Build JSONL, upload to GCS, submit batch. Returns (job_name, gcs_file_name)."""
        ...

    def fetch_batch_results(self, batch_job_name: str) -> list[dict]:
        """Fetch and parse results from completed batch job."""
        ...

    def delete_gcs_file(self, gcs_file_name: str) -> None:
        """Delete input JSONL from GCS via Files API."""
        ...

    # --- JSONL ---

    @staticmethod
    def build_jsonl(entries: list[BatchEntry], cache_map: dict) -> str: ...

    @staticmethod
    def parse_result_jsonl(content: str) -> list[dict]: ...

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

    def store_batch_job(self, batch_job_id: str, gcs_file_name: str) -> None: ...
    def get_batch_job(self, batch_job_id: str) -> dict | None: ...
    def delete_batch_job(self, batch_job_id: str) -> None: ...

    # --- Cache Cleanup ---

    def delete_prompt_caches(self) -> None:
        """Delete all cached content created during this batch lifecycle via client.caches.delete()."""
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

> **NOTE**: Do not implement the lightweight webhook Lambda now. It will be implemented later.

### Architecture

```
Gemini Batch Complete (webhook callback)
         ↓
    Webhook Lambda (NEW, lightweight — TO BE IMPLEMENTED LATER)
       - Extract batch_job_id from Gemini callback
       - Look up batch_job_id in DynamoDB batch_jobs table
       - If status == "finished" → return 200, do nothing
       - Write result payload to S3 as result file
       - Update batch_jobs table: result_file_s3 = key, status = "received"
       - Invoke transcription-reviewer Lambda with { "batch_job_id": "..." }
       - Return 200 immediately
         ↓
    Transcription Reviewer Lambda (EXISTING, new invocation mode)
       - Detect invocation type:
         ├── No event payload / CloudWatch alarm → INITIAL TRIGGER (normal)
         └── event has "batch_job_id"            → RESULT TRIGGER
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

> **NOTE**: This Lambda will be implemented in a future phase. Pseudocode below for reference only.

```python
# webhook_handler.py — lightweight, must return fast to Gemini

def lambda_handler(event, context):
    # 1. Extract batch_job_id and result from Gemini webhook payload
    batch_job_id = extract_job_id(event)
    result_data = extract_result(event)

    # 2. Check DynamoDB — if already finished, return 200 immediately (idempotent)
    job = dynamo.get_item(batch_job_id)
    if job and job["status"] == "finished":
        return {"statusCode": 200}

    # 3. Write result to S3
    result_key = f"batch-results/{batch_job_id}.json"
    s3.put_object(Bucket=RESULTS_BUCKET, Key=result_key, Body=json.dumps(result_data))

    # 4. Update DynamoDB batch_jobs: result_file_s3, status="received"
    dynamo.update_item(batch_job_id, result_file_s3=result_key, status="received")

    # 5. Invoke reviewer Lambda async with batch_job_id
    lambda_client.invoke(
        FunctionName=REVIEWER_FUNCTION_ARN,
        InvocationType="Event",  # async
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
  4. Split file > 5000 words into chunks → BatchEntry list
  5. Store original chunk to TEMPORARY_FIX_BUCKET/{record_id}.original.txt
  6. Register each entry in FIX_TRACKER_TABLE:
     record_id (PK), retry_number=1, completed=false,
     diff_value=999999, original_word_count=len(chunk.split())
  7. Create caches per unique system_prompt (if GEMINI_CACHE_ENABLED)
  8. Build final JSONL from entries
  9. Upload JSONL to GCS via Gemini Files API
  10. Submit batch to Gemini Batch API → get batch_job_id
  11. Store in BATCH_JOBS_TABLE:
      batch_job_id (PK), gcs_file_name, result_file_s3=null, status="submitted"
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
      1. Look up batch_job_id in BATCH_JOBS_TABLE → result_file_s3, gcs_file_name
      2. Fetch results: from S3 (if cached by webhook) or Gemini API directly
      3. Parse result JSONL → list of {key, response_text} entries
      4. Delete old GCS input file (via Gemini Files API)
      5. Delete result file from S3 (if exists)
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
           a. Create caches per unique system_prompt (if GEMINI_CACHE_ENABLED)
           b. Build JSONL from retry entries
           c. Upload to GCS via Files API
           d. Submit new batch to Gemini → new batch_job_id
           e. Store new batch_job_id in BATCH_JOBS_TABLE
           Return StartResult(mode="async", job_ref=new_batch_job_name)

      12. If no retry entries (all chunks done):
           a. Merge split chunks: read {stem}_{1..N}.txt → concatenate → {stem}.txt
           b. Upload merged {stem}.txt to dynamo_entry.media_fixed_transcribed_bucket
           c. Copy .time → .pre-fix.time
           d. Send SQS messages for all completed media_ids
           e. Update MEDIA_TABLE status to "fixed"
           f. Clean up: source files, temporary-fix-files, FIX_TRACKER_TABLE entries
           g. Delete cached content: client.caches.delete(cache.name) 
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
| `gcs_file_name` | S | Input JSONL file name in GCS (via Files API) |
| `result_file_s3` | S | S3 key of result file (set by webhook Lambda) |
| `status` | S | `submitted` → `received` (row deleted after processing) |

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
3. Wire Gemini batch callback URL

---

## Design Decisions

- **No two-layer abstraction.** Each orchestrator owns its full lifecycle — no separate pipeline classes for batch.
- **GeminiBatchService for shared code.** Composition over inheritance. Both batch orchestrators use it, but each controls when/how to call its methods.
- **batch_job_id as constructor param.** `GeminiBatchRetriggerOrchestrator` is constructed with the specific job to process. Single-purpose, testable.
- **Dead-letter on Gemini errors.** When Gemini batch returns errors (status object instead of GenerateContentResponse), the entire media (all chunks of that stem) is copied to `DEAD_LETTER_BUCKET` along with any fixed chunks. Tracker entries and temp files for that stem are cleaned up. Non-error chunks of other stems proceed normally.
- **Reasoning prompt on last retry.** When a chunk reaches its last retry, use `{stem}.template.reasoning.txt` from `context_files_bucket_s3`.
- **Chunk merge on finalize.** Split chunks (`{stem}_{1..N}.txt`) concatenated in order → single `{stem}.txt` → uploaded.
- **Cleanup per result trigger.** Each invocation deletes: BATCH_JOBS row, result file from S3, input JSONL from GCS.
- **JSONL always sends original chunk.** Never a previous LLM result. Goal is best single-pass fix.

## Open Questions

None — all resolved.
