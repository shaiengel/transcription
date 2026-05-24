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
    S3Reader → list .txt files in portal-daf-yomi-transcription
         ↓
    TranscriptionFixer → fetch per-file system prompts from S3 templates
         ↓
    LLMPipeline (abstract)
         ├── BedrockBatchPipeline → AWS Bedrock batch inference (async)
         └── GeminiPipeline → Google Gemini API (sync + immediate results)
         ↓
    Output to final-transcription S3 + SQS notifications
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
    │   └── llm_pipeline.py     # Abstract LLMPipeline base class
    ├── handlers/
    │   └── review.py           # Main orchestration: process_transcriptions()
    ├── services/
    │   ├── s3_reader.py        # List and read transcription files
    │   ├── transcription_fixer.py  # System prompt fetching, stem extraction
    │   ├── token_counter.py    # Token counting for splitting
    │   ├── bedrock_batch_pipeline.py  # AWS Bedrock batch implementation
    │   └── gemini_pipeline.py  # Google Gemini implementation
    ├── infrastructure/
    │   ├── dependency_injection.py  # DI container (comment/uncomment to switch backend)
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
| `GEMINI2.5` | GeminiPipeline | Sync one-by-one | Immediate VTT + TXT + SQS notification |

**Switching backends** — edit `infrastructure/dependency_injection.py`:
```python
# Option 1: AWS Bedrock Batch (AWS_OPUS4.5)
llm_pipeline = providers.Singleton(_create_bedrock_pipeline, ...)

# Option 2: Google Gemini (GEMINI2.5)
# llm_pipeline = providers.Singleton(_create_gemini_pipeline, ...)
```

## System Prompt Management

Each transcription file has its own custom system prompt stored in S3.

**Template location**: `s3://portal-daf-yomi-audio/{stem}.template.txt`

Example for `151415.txt`:
- Transcription: `s3://portal-daf-yomi-transcription/151415.txt`
- Template: `s3://portal-daf-yomi-audio/151415.template.txt`

If a template is missing, the file is skipped and logged as an error.

## Workflow

### AWS Bedrock Batch

1. List all `.txt` files in transcription bucket
2. Fetch per-file system prompt from S3 template
3. Count tokens, split large files, pad batch to 100 entries minimum
4. Upload JSONL to S3, submit batch job to Bedrock
5. Return job ARN — `post_inference` Lambda handles results asynchronously

### Gemini

1. List all `.txt` files in transcription bucket
2. Fetch per-file system prompt from S3 template
3. Split content into ~1000 word chunks at line boundaries (prevents hallucination on long content)
4. Call Gemini per chunk, merge results
5. Read `.time` file with timestamps; inject timestamps into fixed text
6. Upload `{stem}.txt`, `{stem}.vtt`, `{stem}.pre-fix.time` to `final-transcription`
7. Send SQS notification

## Lambda Self-Reinvocation (Timeout Handling)

Processing can exceed the 15-minute Lambda limit. After each file, `context.get_remaining_time_in_millis()` is checked. If less than `TIMEOUT_THRESHOLD_MS` (default 4 min) remains, the Lambda re-invokes itself asynchronously and returns. Processed files are deleted from S3, so the new invocation picks up only remaining files.

- Local dev: `context` is `None` — timeout check is skipped, all files processed.

## Output Files

For each transcription `{stem}.txt` (Gemini output, written to `final-transcription`):

| File | Description |
|------|-------------|
| `{stem}.txt` | LLM-fixed plain text (for RAG) |
| `{stem}.vtt` | VTT subtitles (from fixed text, or original `.time` on mismatch) |
| `{stem}.pre-fix.time` | Original transcription before LLM fix |
| `{stem}.no_timing.txt` | LLM-fixed text when line count mismatched (diagnostic) |

## Configuration

Config files live in `../.config/`. See [.config/README.md](../.config/README.md) for setup.

**Priority**: env vars > `config.secrets.dev.json` > `config.dev.json` > defaults

Relevant keys in `config.dev.json`:
```json
{
  "llm_backend": "AWS_OPUS4.5",
  "gemini_model": "gemini-2.5-flash-lite",
  "s3": {
    "transcription_bucket": "portal-daf-yomi-transcription",
    "template_bucket": "portal-daf-yomi-audio",
    "output_bucket": "final-transcription"
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
| `TRANSCRIPTION_BUCKET` | `portal-daf-yomi-transcription` | Source bucket |
| `TEMPLATE_BUCKET` | `portal-daf-yomi-audio` | System prompt templates |
| `OUTPUT_BUCKET` | `final-transcription` | Output bucket (Gemini backend) |
| `LLM_BACKEND` | `AWS_OPUS4.5` | Backend: `AWS_OPUS4.5` or `GEMINI2.5` |
| `BATCH_MODEL_ID` | `us.anthropic.claude-opus-4-5-20251101-v1:0` | Bedrock model |
| `BATCH_ROLE_ARN` | — | Bedrock batch IAM role ARN |
| `GOOGLE_API_KEY` | — | Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Gemini model name |
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

# Deploy to Lambda (build wheel first)
uv build
# Upload dist/*.whl to Lambda via AWS Console or CI
```

## IAM Role: `portal-reviewer-role`

**Permissions:**
- `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, `s3:DeleteObject` — transcription, template, output buckets
- `bedrock:InvokeModel` — Bedrock runtime
- `bedrock:CreateModelInvocationJob`, `bedrock:GetModelInvocationJob` — Bedrock batch
- `sqs:SendMessage` — results queue
- `lambda:InvokeFunction` — self-reinvocation (`arn:aws:lambda:us-east-1:707072965202:function:transcription-reviewer`)
- CloudWatch Logs

**Trust**: `lambda.amazonaws.com`

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing S3 template | File skipped, logged as error, counted in `failed_to_load` |
| Failed transcription read | File skipped, logged |
| Gemini API error | File counted as failed, loop continues |
| Line count mismatch (Gemini) | Uses original `.time` for VTT, saves `{stem}.no_timing.txt` |
