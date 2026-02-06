# Post Inference Lambda

AWS Lambda function that processes Bedrock batch inference output. Triggered by EventBridge when a batch job completes, it matches LLM-fixed text with original timestamped transcriptions to produce VTT subtitle files.

## How It Works

1. **EventBridge** triggers Lambda when Bedrock batch job status becomes `Completed`
2. Lambda calls `GetModelInvocationJob` to find the output S3 location
3. Downloads and parses the `.jsonl.out` batch output file
4. For each record (skipping `dummy_*` padding entries):
   - Merges split records (`stem_1`, `stem_2` → `stem`)
   - Reads `{stem}.time` from `portal-daf-yomi-transcription`
   - Injects timestamps into the LLM-fixed text
   - Creates VTT subtitle file
   - Uploads results to `final-transcription` bucket
   - Sends SQS notification with VTT filename

## Output Files

For each processed stem, the following files are written to the `final-transcription` bucket:

| File | Always | Description |
|------|--------|-------------|
| `{stem}.vtt` | Yes | VTT subtitles |
| `{stem}.txt` | Yes | LLM-fixed plain text (for RAG) |
| `{stem}.pre-fix.time` | Yes | Original transcription before LLM fix |
| `{stem}.no_timing.txt` | On mismatch | LLM-fixed text when line counts didn't match |

When timestamps inject successfully, the VTT contains the LLM-fixed text with original timestamps. When line counts mismatch, the VTT falls back to the original `.time` file content, and the LLM output is saved as `.no_timing.txt` for review.

## EventBridge Rule

Filter pattern to only trigger on successful completions:

```json
{
  "source": ["aws.bedrock"],
  "detail-type": ["Batch Inference Job State Change"],
  "detail": {
    "status": ["Completed"]
  }
}
```

## Setup

### 1. Install dependencies

```bash
cd post_inference
uv sync
```

### 2. Configure environment

The `.env` file is generated from `.env.jinja` using the central config system:

```bash
cd .config
render_env.bat dev --skip-validation
```

Or manually create `post_inference/.env`:

```
AWS_PROFILE_POST_REVIEWER=post_reviewer
AWS_REGION=us-east-1
TRANSCRIPTION_BUCKET=portal-daf-yomi-transcription
OUTPUT_BUCKET=final-transcription
AUDIO_BUCKET=portal-daf-yomi-audio
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/707072965202/sqs-fix-transcribes
```

### 3. AWS profile (local development)

Add to `~/.aws/config`:

```ini
[profile post_reviewer]
role_arn = arn:aws:iam::707072965202:role/portal-post-reviewer-role
source_profile = default
region = us-east-1
```

### 4. Local testing

Edit `local_test.py` with a real batch job ARN, then:

```bash
uv run python local_test.py
```

## IAM Role

**Role name**: `portal-post-reviewer-role`

**Permissions**:
- `bedrock:GetModelInvocationJob` on `arn:aws:bedrock:us-east-1:707072965202:model-invocation-job/*`
- `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, `s3:DeleteObject` on transcription and audio buckets
- `s3:PutObject` on `final-transcription` bucket
- `sqs:SendMessage` on `arn:aws:sqs:us-east-1:707072965202:sqs-fix-transcribes`
- CloudWatch Logs (`logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`)

**Trust policy**: `lambda.amazonaws.com`

## Lambda Configuration

| Setting | Value |
|---------|-------|
| Handler | `post_inference.handler.lambda_handler` |
| Runtime | Python 3.12 |
| Memory | 512 MB |
| Timeout | 5 minutes |
