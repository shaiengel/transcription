# Transcription Reviewer

Lambda function that reviews and fixes raw transcriptions using an LLM (AWS Bedrock or Google Gemini). Runs after `gpu_instance` finishes transcribing, before `gpu_timestamp` re-aligns timestamps.

## Pipeline Position

```
gpu_instance → [portal-daf-yomi-transcription S3] → transcription_reviewer → [final-transcription S3] → gpu_timestamp
```

## AWS Trigger

**CloudWatch Alarm** fires when the `portal-transcription-fix` ASG scales to 0 (i.e., the GPU transcription worker has finished and shut down). This signals that new `.txt` files are ready in the transcription bucket.

- Trigger: CloudWatch Alarm on ASG `portal-transcription-fix` capacity = 0
- Runs as: AWS Lambda (`transcription-reviewer`)
- IAM Role: `portal-reviewer-role`
- Timeout: 15 minutes (with self-reinvocation for larger batches)

## Architecture

```
CloudWatch Alarm (ASG scaled to 0)
         ↓
    Lambda handler
         ↓
    S3Reader → list .txt files in TRANSCRIPTION_BUCKET
         ↓
    DynamoReader → get_entry(media_id) — gate on status == "transcribed"
         ↓
    TranscriptionFixer → fetch system prompt from context_files_bucket_s3
         ↓
    LLMPipeline (abstract)
         ├── BedrockBatchPipeline → AWS Bedrock batch inference (async)
         └── GeminiPipeline → Google Gemini API (sync + immediate results)
         ↓
    Output to media_fixed_transcribed_bucket S3 + SQS notification
         ↓
    DynamoReader → set_status(media_id, "fixed")
```

## Project Structure

```
transcription_reviewer/
├── pyproject.toml
├── .env                        # Rendered from .env.jinja (see .config/)
├── .env.jinja                  # Template for environment variables
├── local_test.py               # Local testing script
└── src/transcription_reviewer/
    ├── __init__.py
    ├── handler.py              # Lambda entry point
    ├── config.py               # Configuration management (JSON + env vars)
    ├── models/
    │   ├── schemas.py          # TranscriptionFile, ReviewResult
    │   ├── dynamo_entry.py     # DynamoDBMediaEntry dataclass
    │   └── llm_pipeline.py     # Abstract LLMPipeline base class
    ├── handlers/
    │   └── review.py           # Main orchestration: process_transcriptions()
    ├── services/
    │   ├── s3_reader.py        # List and read transcription files
    │   ├── dynamo_reader.py    # DynamoDB get_entry / set_status
    │   ├── transcription_fixer.py  # System prompt fetching, stem extraction
    │   ├── token_counter.py    # Token counting for splitting
    │   ├── bedrock_batch_pipeline.py  # AWS Bedrock batch implementation
    │   └── gemini_pipeline.py  # Google Gemini implementation
    ├── infrastructure/
    │   ├── dependency_injection.py  # DI container (comment/uncomment to switch backend)
    │   ├── dynamodb_client.py
    │   ├── s3_client.py
    │   ├── sqs_client.py
    │   ├── bedrock_client.py
    │   └── bedrock_batch_client.py
    └── utils/
        └── vtt_converter.py    # Timed text → VTT format conversion
```

## LLM Backends

Configured via `LLM_BACKEND` env var or `llm_backend` in `config.dev.json`.

| Backend | Implementation | Processing | Output |
|---------|---------------|------------|--------|
| `AWS_OPUS4.5` | BedrockBatchPipeline | Async batch (≥100 entries) | Job ARN — `post_inference` Lambda processes results |
| `GEMINI2.5` | GeminiPipeline | Sync one-by-one | Immediate TXT + SQS notification |

**Switching backends** — edit `infrastructure/dependency_injection.py`:
```python
# Option 1: AWS Bedrock Batch (AWS_OPUS4.5)
llm_pipeline = providers.Singleton(_create_bedrock_pipeline, ...)

# Option 2: Google Gemini (GEMINI2.5)
# llm_pipeline = providers.Singleton(_create_gemini_pipeline, ...)
```

## DynamoDB Integration (Gemini backend only)

Each file's S3 bucket paths and processing status are stored in a DynamoDB table (shared with `gpu_instance` and `audio_manager`).

**Table**: `MEDIA_TABLE` env var (e.g. `transcription-tracker`)  
**Partition key**: `media_id` (string — the numeric filename stem)

Relevant fields read per entry:

| DynamoDB field | Used for |
|----------------|----------|
| `status` | Gate: only process if `"transcribed"` |
| `context_files_bucket_s3` | Bucket containing `{stem}.template.txt` system prompt |
| `media_transcribed_bucket` | Source bucket for `.time` file copy |
| `media_fixed_transcribed_bucket` | Destination bucket for fixed `.txt` output |

After a successful fix, status is updated to `"fixed"`.

## System Prompt Management

Each transcription file has its own custom system prompt stored in S3.

**Template location**: `s3://{context_files_bucket_s3}/{stem}.template.txt`

The bucket is resolved per-file from the DynamoDB entry (`context_files_bucket_s3`). If the template is missing, the file is skipped and logged as an error.

## Workflow (Gemini)

1. List all `.txt` files in `TRANSCRIPTION_BUCKET`
2. For each file, look up its DynamoDB entry by `media_id` (filename stem as int)
3. Skip silently if status is not `"transcribed"`
4. Fetch system prompt from `context_files_bucket_s3`
5. Optionally truncate at long silence segments (`.time` file)
6. Split content into word chunks if needed
7. Call Gemini per chunk with cached system prompt, merge results
8. Upload fixed `{stem}.txt` to `media_fixed_transcribed_bucket`
9. Copy original `.time` as `{stem}.pre-fix.time` then delete source files
10. Send SQS notification `{"media_id": <int>}`
11. Update DynamoDB status to `"fixed"`

## Workflow (Bedrock Batch)

1. List all `.txt` files in transcription bucket
2. Fetch per-file system prompt from S3 template
3. Count tokens, split large files, pad batch to 100 entries minimum
4. Upload JSONL to S3, submit batch job to Bedrock
5. Return job ARN — `post_inference` Lambda handles results asynchronously

## Lambda Self-Reinvocation (Timeout Handling)

Processing can exceed the 15-minute Lambda limit. After each file, `context.get_remaining_time_in_millis()` is checked. If less than `TIMEOUT_THRESHOLD_MS` (default 4 min) remains, the Lambda re-invokes itself asynchronously and returns. Processed files are deleted from S3, so the new invocation picks up only remaining files.

- Local dev: `context` is `None` — timeout check is skipped, all files processed.

## Crash Detection & Retry Logic (Gemini backend)

The `transcription-fix-tracker` DynamoDB table prevents duplicate processing and lets a new Lambda resume a crashed one.

**Table key**: `media_id`  
**Fields**: `retry_number`, `lambda_started_at`, `lambda_time_remaining_ms`, `current_chunk`, `total_chunks`

### Per-file flow

1. **Claim** — `try_claim` writes the entry with `condition="attribute_not_exists(media_id)"`.
   - Success → this Lambda owns the file, `retry_number=1`.
   - Fail → another Lambda already claimed it, read the existing entry.

2. **Active check** — if `now - lambda_started_at < 18 min`, another Lambda is still alive → skip.

3. **Crashed** — elapsed ≥ 18 min means the previous Lambda died. Increment `retry_number`.

4. **Slow LLM check** — if the dead Lambda had `lambda_time_remaining_ms > 14 min` when it last wrote, the LLM call itself was too slow (not a timeout issue):
   - If `retry_number >= 4` → **dead-letter**: move source file to `transcription-dead-letter` and skip. This is the **only** case that triggers dead-lettering.
   - If `retry_number < 4` → continue retrying normally.

5. **Normal timeout** — Lambda ran out of time mid-chunk; **always retry, never dead-letter**, regardless of retry count.

6. **Resume** — update tracker with new `retry_number`, `lambda_started_at`, and reset `current_chunk=1`. Each chunk re-loads its best S3 result (`{stem}_{i}.txt`) as a baseline before running new Gemini attempts.

### Per-chunk retry

`retry_number` is the attempt floor — `range(retry_number, 5)` so later Lambdas don't repeat already-passed attempts:
- Lambda 1 (`retry_number=1`): 4 Gemini attempts
- Lambda 2 (`retry_number=2`): 3 attempts
- Lambda 3 (`retry_number=3`): 2 attempts
- Lambda 4 (`retry_number=4`): dead-letter (slow LLM) or 1 attempt (timeout)

**S3 as source of truth** — whenever a Gemini attempt produces a better result (lower word-count diff vs original), it overwrites `{stem}_{i}.txt` in `temporary-fix-files`. A resuming Lambda loads that file first; it only runs new attempts if the existing result doesn't yet meet the threshold.

### Cleanup

On success, `delete_entry` removes the tracker row. The file is then deleted from the transcription bucket so the next Lambda invocation skips it.

## Output Files

For each transcription `{stem}.txt` (Gemini output, written to `media_fixed_transcribed_bucket`):

| File | Description |
|------|-------------|
| `{stem}.txt` | LLM-fixed plain text |
| `{stem}.pre-fix.time` | Original timed transcription before LLM fix |

## Configuration

Config files live in `../.config/`. See [.config/README.md](../.config/README.md) for setup.

**Priority**: env vars > `config.secrets.dev.json` > `config.dev.json` > defaults

Relevant keys in `config.dev.json`:
```json
{
  "llm_backend": "GEMINI2.5",
  "gemini_model": "gemini-2.5-flash",
  "s3": {
    "transcription_bucket": "portal-daf-yomi-transcription"
  },
  "dynamodb": {
    "table_name": "transcription-tracker"
  },
  "bedrock": {
    "batch_model_id": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "batch_role_arn": "arn:aws:iam::707072965202:role/portal-bedrock-batch"
  }
}
```

`config.secrets.dev.json` (not committed):
```json
{ "google_api_key": "your-api-key-here" }
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `AWS_PROFILE_REVIEWER` | `reviewer` | AWS profile (local dev only) |
| `TRANSCRIPTION_BUCKET` | `portal-daf-yomi-transcription` | Source bucket for file discovery |
| `MEDIA_TABLE` | — | DynamoDB table name (required for Gemini backend) |
| `LLM_BACKEND` | `AWS_OPUS4.5` | Backend: `AWS_OPUS4.5` or `GEMINI2.5` |
| `BATCH_MODEL_ID` | `us.anthropic.claude-opus-4-5-20251101-v1:0` | Bedrock model |
| `BATCH_ROLE_ARN` | — | Bedrock batch IAM role ARN |
| `GOOGLE_API_KEY` | — | Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `SQS_QUEUE_URL` | — | Results queue URL (Gemini backend) |
| `MIN_ENTRIES` | `100` | Minimum batch size (Bedrock, padded with dummies) |
| `MAX_TOKENS` | `60000` | Token limit per entry before splitting |
| `TEMPERATURE` | `0.1` | LLM temperature |
| `TIMEOUT_THRESHOLD_MS` | `240000` | ms remaining before Lambda self-reinvokes |

## Commands

```bash
# Render .env from template
cd transcription_reviewer
cmd /c ..\.config\render_env.bat dev --require-secrets

# Run local test
python local_test.py

# Deploy to Lambda
./deploy/deploy.ps1
```

## IAM Role: `portal-reviewer-role`

**Permissions:**
- `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, `s3:DeleteObject` — transcription, template, output buckets
- `dynamodb:GetItem`, `dynamodb:UpdateItem` — media tracking table
- `bedrock:InvokeModel` — Bedrock runtime
- `bedrock:CreateModelInvocationJob`, `bedrock:GetModelInvocationJob` — Bedrock batch
- `sqs:SendMessage` — results queue
- `lambda:InvokeFunction` — self-reinvocation (`arn:aws:lambda:us-east-1:707072965202:function:transcription-reviewer`)
- CloudWatch Logs

**Trust**: `lambda.amazonaws.com`

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No DynamoDB entry for media_id | File counted as failed, loop continues |
| Status not `"transcribed"` | File skipped silently (not counted as failure) |
| Missing S3 template | File skipped, logged as error, counted as failed |
| Failed transcription read | File skipped, logged as failed |
| Gemini API error | File counted as failed, loop continues |
