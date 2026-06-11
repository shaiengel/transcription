# Architecture: Batch Orchestrator for Transcription Review

## Problem

`process_transcriptions()` in `handlers/review.py` is coupled to per-file synchronous processing. Each file goes through `prepare_data → invoke → post_process` individually. This works for on-demand Gemini calls but is inefficient and structurally incompatible with batch APIs (Gemini Batch, Bedrock Batch) where you want to:

1. Collect all work upfront
2. Submit one batch request
3. Receive results asynchronously (webhook)
4. Potentially iterate (retry/reasoning) at batch level
5. Finalize all files at once (S3 + SQS)

## Solution: Two-Layer Architecture

### Layer 1: ReviewOrchestrator (NEW — controls lifecycle)

Abstract class with two implementations. Sits ABOVE `LLMPipeline`. Controls when and how `process_transcriptions` runs.

```
ReviewOrchestrator (abstract)
  ├── prepare(s3_reader, transcription_fixer, dynamo_reader, bucket, context)
  ├── start()
  │
  ├── OnDemandOrchestrator
  │     uses: GeminiPipeline (existing)
  │
  └── BatchOrchestrator
        uses: GeminiBatchPipeline (NEW)
```

### Layer 2: LLMPipeline (EXISTING — handles LLM interaction)

```
LLMPipeline (abstract)
  ├── GeminiPipeline         ← existing, on-demand
  ├── GeminiBatchPipeline    ← NEW, builds JSONL + submits batch
  └── BedrockBatchPipeline   ← existing, batch
```

---

## Orchestrator API

All orchestrator methods use `config.py` for S3 buckets, DynamoDB table, and other environment params. No need to pass infrastructure dependencies into `prepare()`/`start()`.

```python
class ReviewOrchestrator(ABC):

    @abstractmethod
    def prepare(self, context=None) -> PrepareResult:
        """Collect files, validate, build work items. May invoke pipeline."""
        pass

    @abstractmethod
    def start(self) -> StartResult:
        """Execute the processing. Returns immediately for batch (async)."""
        pass
```

### OnDemandOrchestrator

```python
class OnDemandOrchestrator(ReviewOrchestrator):
    """Wraps existing process_transcriptions() flow. All deps resolved from DI container."""

    def prepare(self, context=None):
        # No-op — on-demand processes files one by one during start()
        self._context = context
        return PrepareResult(file_count=0, action="deferred")

    def start(self):
        # Calls existing process_transcriptions() unchanged
        # s3_reader, pipeline, transcription_fixer, dynamo_reader, bucket
        # all resolved internally from DI container / config
        result = process_transcriptions(...)
        return StartResult.from_review_result(result)
```

### BatchOrchestrator

```python
class BatchOrchestrator(ReviewOrchestrator):
    """Batch mode. All deps resolved from DI container."""

    def prepare(self, context=None):
        # Phase 1: Collect all files
        #   - list files from S3 (config.transcription_bucket)
        #   - DynamoDB gate (status == "transcribed")
        #   - fetch system prompts
        #   - build TranscriptionFile list
        #   - split files > 5000 words into chunks
        # Phase 2: Feed all files to GeminiBatchPipeline
        #   - pipeline.prepare_data(all_files) → builds JSONL entries
        #   - register each entry in DynamoDB (batch_entries table)
        #   - stores entries internally
        self._batch_entries = self._collect_and_prepare(...)
        return PrepareResult(file_count=len(self._batch_entries), action="batch_ready")

    def start(self):
        # Submit batch to Gemini Batch API
        job_ref = self._pipeline.invoke(self._batch_entries)
        return StartResult(job_ref=job_ref, mode="async")
```

---

## GeminiBatchPipeline (NEW)

New child of `LLMPipeline`. Analogous to `BedrockBatchPipeline` but targets Gemini Batch API.

Input JSONL is uploaded to GCS via the [Gemini Files API](https://ai.google.dev/gemini-api/docs/files). The Files API handles storage — no separate GCS bucket setup needed.

```python
class GeminiBatchPipeline(LLMPipeline):

    def prepare_data(self, files: list[TranscriptionFile], context=None) -> list[BatchEntry]:
        # Token counting, split files > 5000 words into chunks
        # Returns BatchEntry list (no JSONL yet)
        ...

    def invoke(self, prepared_data: list[BatchEntry], context=None) -> str:
        # 1. Group entries by system_prompt
        # 2. For each unique system_prompt (if GEMINI_CACHE_ENABLED):
        #    - Create cached content via Gemini API (same as GeminiPipeline._get_or_create_cache)
        #    - Store cache name for JSONL generation
        # 3. Create JSONL — each entry includes cached_content reference
        #    (entries with reasoning prompt are NOT cached — different system prompt)
        # 4. Upload JSONL to GCS via Gemini Files API
        # 5. Register each entry in FIX_TRACKER_TABLE
        # 6. Submit batch job to Gemini Batch API
        # 7. Store batch_job_id in BATCH_JOBS_TABLE
        #    (with gcs_file_name, status="submitted")
        # 8. Return batch job ID/name
        ...

    def post_process(self, llm_response, original_files) -> ReviewResult:
        # Called by result trigger path, not inline
        # Returns ReviewResult with batch reference
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
  "request": {
    "model": "models/gemini-2.5-flash",
    "cached_content": "cachedContents/abc123",
    "contents": [{"role": "user", "parts": [{"text": "...transcription chunk..."}]}],
    "generation_config": {"temperature": 0.1, "top_p": 0.1, "top_k": 1, "max_output_tokens": 60000}
  }
}
```

**JSONL entry format (uncached — reasoning or cache disabled):**
```json
{
  "request": {
    "model": "models/gemini-2.5-flash",
    "system_instruction": {"parts": [{"text": "...reasoning system prompt..."}]},
    "contents": [{"role": "user", "parts": [{"text": "...transcription chunk..."}]}],
    "generation_config": {"temperature": 0.1, "top_p": 0.1, "top_k": 1, "max_output_tokens": 60000}
  }
}
```

### Diff Strategy (Quality Check)

Word-count diff logic extracted to `utils/word_diff.py` (shared by both on-demand `GeminiPipeline` and `GeminiBatchPipeline`):

```python
# utils/word_diff.py
def word_count_diff(text: str, original_word_count: int) -> int:
    return abs(len(text.split()) - original_word_count)
```

- `original_word_count` is stored in FIX_TRACKER_TABLE at initial trigger time
- `max_word_diff` threshold (default 100) from config determines "good enough"
- JSONL always sends the **original chunk** (never a previous LLM result) — the goal is to get the best single-pass fix
- Each result trigger compares `new_diff` against the stored `diff_value` in FIX_TRACKER_TABLE to decide whether to keep the new result or the previous best
- Best result per chunk stored at `TEMPORARY_FIX_BUCKET/{stem}_{chunk_idx}.txt` (split files) or `TEMPORARY_FIX_BUCKET/{stem}.txt` (unsplit)

### Key difference from BedrockBatchPipeline

| Aspect | BedrockBatchPipeline | GeminiBatchPipeline |
|--------|---------------------|---------------------|
| Storage | S3 JSONL | GCS via Gemini Files API |
| Submission | `CreateModelInvocationJob` | Gemini Batch API |
| Results | `post_inference` Lambda | Webhook Lambda → reviewer Lambda |
| Retry logic | None (single pass) | Multi-iteration with quality check |
| System prompt | Per-entry in JSONL | Cached content (or inline for reasoning/disabled) |

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

### Initial Trigger Flow (batch mode)

```
1. List .txt files from S3
2. DynamoDB gate on MEDIA_TABLE (status == "transcribed")
3. Fetch system prompts
4. Split files > 5000 words into chunks
5. Build JSONL entries
6. Register each entry in FIX_TRACKER_TABLE:
   - record_id (PK), retry_number=1, completed=false,
     diff_value=999999, original_word_count=len(chunk.split())
8. Upload JSONL to GCS via Gemini Files API
9. Submit batch to Gemini Batch API → get batch_job_id
10. Store in BATCH_JOBS_TABLE:
    - batch_job_id (PK), gcs_file_name, result_file_s3=null, status="submitted"
```

### Result Trigger Flow

```
Reviewer Lambda receives { "batch_job_id": "..." }
         ↓
    1. Look up batch_job_id in BATCH_JOBS_TABLE
       → get result_file_s3, gcs_file_name
         ↓
    2. Read result file from S3
         ↓
    3. Delete input JSONL from GCS (via Gemini Files API, using gcs_file_name)
         ↓
    4. Delete result file from S3
         ↓
    5. Delete batch_job_id row from BATCH_JOBS_TABLE
         ↓
    6. Per-entry diff calculation:
       - Use utils/word_diff.py: word_count_diff(result_text, original_word_count)
       - Compare against max_word_diff threshold (default 100, from config)
       - Parse errors / empty responses treated as diff=999999
         ↓
    7. For each entry:
       - Read stored diff_value and original_word_count from FIX_TRACKER_TABLE
       - Calculate new_diff = abs(len(result_text.split()) - original_word_count)
       - JSONL always contains the ORIGINAL chunk text, never a previous result
       │
       ├── new_diff <= max_word_diff (GOOD):
       │     - Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt
       │     - Update FIX_TRACKER_TABLE: completed=true, diff_value=new_diff
       │     - No retry needed
       │
       ├── new_diff < stored diff_value (BETTER but not good enough):
       │     - Write result to TEMPORARY_FIX_BUCKET/{record_id}.txt (replace previous)
       │     - Update FIX_TRACKER_TABLE: diff_value=new_diff, retry_number++
       │     - Add to retry JSONL (with ORIGINAL chunk, not result)
       │     - If last retry → use reasoning prompt for this chunk
       │
       └── new_diff >= stored diff_value (WORSE or equal):
             - Keep existing file in TEMPORARY_FIX_BUCKET unchanged
             - Update FIX_TRACKER_TABLE: retry_number++ (diff_value stays)
             - Add to retry JSONL (with ORIGINAL chunk)
             - If last retry → use reasoning prompt for this chunk
         ↓
    8. After processing all entries:
       ├── Retry JSONL not empty:
       │     - Upload new JSONL to GCS via Files API
       │     - Submit new batch to Gemini → new batch_job_id
       │     - Insert new row in BATCH_JOBS_TABLE
       └── Retry JSONL empty (all chunks done):
             - Merge split chunks: read {stem}_{1..N}.txt → concatenate → {stem}.txt
             - Upload merged {stem}.txt to dynamo_entry.media_fixed_transcribed_bucket
             - Copy .time → .pre-fix.time
             - Send SQS messages for all completed media_ids
             - Update MEDIA_TABLE status to "fixed"
             - Clean up: source files, temporary-fix-files, FIX_TRACKER_TABLE entries
```

### DynamoDB Tables

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
| `record_id` | S (PK) | `{stem}` or `{stem}_{chunk_idx}` if split |
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

## Modified Lambda Handler

```python
# handler.py — updated

def lambda_handler(event, context):
    container = DependenciesContainer()
    orchestrator = container.orchestrator()

    # Detect invocation type
    batch_job_id = event.get("batch_job_id") if isinstance(event, dict) else None

    if batch_job_id:
        # RESULT TRIGGER — webhook Lambda forwarded batch_job_id after Gemini completed
        result = orchestrator.handle_batch_results(batch_job_id, context)
    else:
        # INITIAL TRIGGER — CloudWatch alarm or manual invocation
        prepare_result = orchestrator.prepare(context=context)
        start_result = orchestrator.start()

        if start_result.timed_out:
            _reinvoke_self(context)

        result = start_result

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

## File Changes Summary

### New Files

| File | Description |
|------|-------------|
| `models/review_orchestrator.py` | `ReviewOrchestrator` abstract class + `PrepareResult`, `StartResult` |
| `handlers/on_demand_orchestrator.py` | `OnDemandOrchestrator` — wraps existing flow |
| `handlers/batch_orchestrator.py` | `BatchOrchestrator` — collect → batch → async |
| `services/gemini_batch_pipeline.py` | `GeminiBatchPipeline(LLMPipeline)` |
| `webhook_handler.py` | Webhook Lambda — receives Gemini callback, writes S3, invokes reviewer |
| `utils/word_diff.py` | Shared word-count diff function (extracted from `GeminiPipeline`) |

### Modified Files

| File | Change |
|------|--------|
| `handler.py` | Detect invocation type (initial vs result trigger), use orchestrator |
| `infrastructure/dependency_injection.py` | Add orchestrator factory, `GeminiBatchPipeline` wiring |
| `config.py` | Add batch_jobs_table name, results bucket |
| `handlers/review.py` | No change — `process_transcriptions()` still used by `OnDemandOrchestrator` |

### Unchanged Files

| File | Reason |
|------|--------|
| `services/gemini_pipeline.py` | Refactored to use `utils/word_diff.py` for diff calculation |
| `services/bedrock_batch_pipeline.py` | Separate backend, unaffected |
| `models/llm_pipeline.py` | Abstract interface unchanged |
| `handlers/review.py` | Reused by `OnDemandOrchestrator.start()` |

---

## Migration Path

### Phase 1: Orchestrator Scaffold
1. Create `ReviewOrchestrator` abstract class
2. Implement `OnDemandOrchestrator` wrapping existing flow
3. Update `handler.py` to use orchestrator
4. **Zero behavior change** — on-demand path works exactly as before

### Phase 2: Gemini Batch Pipeline
1. Implement `GeminiBatchPipeline` (JSONL creation + Gemini Batch API submission)
2. Implement `BatchOrchestrator` (collect files → delegate to pipeline)
3. Add DI wiring + config flag

### Phase 3: Result Trigger + DynamoDB
1. Add result trigger detection in `handler.py` (batch_job_id in event)
2. Implement `BatchOrchestrator.handle_batch_results()`:
   - Quality check per chunk (word-diff threshold)
   - Reasoning prompt on last retry
   - Re-batch failures or finalize (merge chunks → upload → SQS)
3. Create BATCH_JOBS_TABLE in DynamoDB
4. Reuse FIX_TRACKER_TABLE for per-chunk retry tracking
5. Cleanup logic: delete batch_job row, S3 result file, GCS input JSONL

### Phase 4: Webhook Lambda (LATER)
1. Implement `webhook_handler.py` (lightweight Lambda)
2. Deploy webhook Lambda + API Gateway endpoint
3. Wire Gemini batch callback URL

### Phase 5: Cleanup
1. Remove `BedrockBatchPipeline` if fully migrated (optional)
2. Update README.md with new architecture diagram
3. Add integration tests for batch flow

---

## Design Decisions

- **No dead-lettering in batch mode.** Chunks always finalize with best-effort result after max retries.
- **Reasoning prompt on last retry.** When a chunk reaches its last retry, use `{stem}.template.reasoning.txt` from `context_files_bucket_s3` (same as on-demand last-attempt logic).
- **Chunk merge on finalize.** Split chunks (`{stem}_{1..N}.txt`) are concatenated in order → single `{stem}.txt` → uploaded to `dynamo_entry.media_fixed_transcribed_bucket`.
- **Cleanup per result trigger.** Each invocation deletes: batch_job_id row from BATCH_JOBS_TABLE, result file from S3, input JSONL from GCS.

## Open Questions

None — all resolved.
