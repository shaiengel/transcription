# GPU Timestamp Alignment Worker

GPU-based worker that re-aligns LLM-corrected transcriptions with original audio using stable-whisper. After the LLM reviewer fixes spelling and grammar, word timestamps drift from the audio — this worker corrects them.

## Pipeline Position

```
transcription_reviewer → [sqs-fix-transcribes SQS] → gpu_timestamp → [final-transcription S3] → transcribe_reader
```

## AWS Trigger

**SQS message** on `sqs-fix-transcribes` triggers the `portal-timestamp-fix` Auto Scaling Group to launch an EC2 Spot instance. The container polls the queue continuously and self-terminates when the queue is empty for 5 minutes.

- Trigger: SQS `sqs-fix-transcribes` has messages → CloudWatch Alarm → ASG `portal-timestamp-fix` scale to 2
- Scale-down: CloudWatch Alarm (visible + in-flight = 0 for 5 minutes) → scale to 0
- Runs on: EC2 GPU Spot instance
- IAM: EC2 instance profile with S3 + SQS permissions

## Architecture

```
SQS (sqs-fix-transcribes) → Docker Container → S3 (per-entry output bucket)
                              ├── SQSReceiver (polls messages)
                              ├── DynamoReader (fetches bucket names + language from transcription-tracker)
                              ├── S3Downloader (audio + text, buckets from DynamoDB)
                              ├── AlignmentEvaluator (pre-alignment DTW fix)
                              ├── Aligner (stable-whisper alignment)
                              ├── AlignmentEvaluator (post-alignment quality check)
                              ├── S3Uploader (results to output bucket from DynamoDB)
                              └── SQSSender (completion to sqs-final-transcribes)
```

## Project Structure

```
gpu_timestamp/
├── Dockerfile              # CUDA 12.4 + Ubuntu 22.04 + Python 3.12
├── user_data.yaml          # EC2 cloud-init script
├── pyproject.toml
├── .env
└── src/gpu_timestamp/
    ├── main.py             # Entry point — loads model, starts worker loop
    ├── config.py           # Environment configuration
    ├── handlers/
    │   └── alignment.py    # Worker loop, message orchestration
    ├── services/
    │   ├── aligner.py      # stable-whisper alignment logic
    │   ├── alignment_evaluator.py  # DTW + probability-based quality checks
    │   ├── s3_downloader.py
    │   ├── s3_uploader.py
    │   ├── sqs_receiver.py
    │   └── sqs_sender.py
    ├── infrastructure/
    │   ├── dependency_injection.py
    │   ├── dynamodb_client.py
    │   ├── s3_client.py
    │   └── sqs_client.py
    ├── services/
    │   └── dynamo_reader.py
    └── models/
        ├── dynamo_entry.py  # DynamoDBMediaEntry — per-entry bucket config
        └── schemas.py       # SQSMessage, AlignmentResult
```

## Processing Flow

For each SQS message `{"media_id": 151415}`:

1. **Fetch entry** — query DynamoDB `transcription-tracker` for `media_id` to get `audio_bucket`, `text_bucket`, `output_bucket`, and `language`
2. **Download audio** from `{audio_bucket}/{stem}.mp3`
3. **Download corrected text** from `{text_bucket}/{stem}.txt`
4. **Pre-alignment DTW fix** (if enabled) — compares old `.pre-fix.time` with corrected text to detect and remove hallucinated lines at the end
5. **Align** — stable-whisper aligns text to audio, producing word-level timestamps
6. **Post-alignment evaluation** — detects timestamp degradation using rolling average of word probabilities + CUSUM (cumulative sum control chart)
7. **Truncate if needed** — if quality degrades, output is truncated at the cutoff point
8. **Upload** `.json`, `.vtt`, `.srt` to `{output_bucket}`
9. **Set status** — update DynamoDB entry status to `"aligned"`
10. **Notify** — send completion message to `sqs-final-transcribes`

## Output Files

Written to the per-entry `output_bucket` from DynamoDB (`media_subtitles` field):

| File | Description |
|------|-------------|
| `{stem}.vtt` | VTT subtitles with corrected timestamps |
| `{stem}.srt` | SRT subtitles |
| `{stem}.json` | Detailed alignment output (word-level timestamps) |
| `{stem}.dtw.txt` | DTW-filtered text used for alignment (if DTW enabled) |
| `{stem}.analysis` | Truncation analysis (only written if truncation was applied) |

## Docker Build & Push

S3 is used instead of ECR to store the image in order to reduce cost.

```bash
docker build -t whisper-timestamp:1 .
docker save whisper-timestamp:1 | gzip > whisper-timestamp.tar.gz
aws s3 cp whisper-timestamp.tar.gz s3://portal-docker-images/whisper-timestamp.tar.gz --profile portal
```

## EC2 Deployment

**User data** (`user_data.yaml`) runs on instance start:
1. Downloads Whisper model from `s3://portal-daf-yomi-models/whisper-large-v3.pt`
2. Loads Docker image from `s3://portal-docker-images/whisper-timestamp.tar.gz`
3. Runs container with `--gpus all`

### ASG Scaling Setup

The `portal-timestamp-fix` ASG scales on `sqs-fix-transcribes` queue depth.

```bash
# Scale to 0 when queue empty (visible + in-flight = 0 for 5 consecutive minutes)
aws cloudwatch put-metric-alarm \
  --alarm-name "scale to 0" \
  --evaluation-periods 5 \
  --comparison-operator LessThanOrEqualToThreshold \
  --threshold 0 \
  --metrics '[
    {"Id":"m1","MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"sqs-fix-transcribes"}]},"Period":60,"Stat":"Average"},"ReturnData":false},
    {"Id":"m2","MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesNotVisible","Dimensions":[{"Name":"QueueName","Value":"sqs-fix-transcribes"}]},"Period":60,"Stat":"Average"},"ReturnData":false},
    {"Id":"total","Expression":"m1 + m2","Label":"TotalMessages","ReturnData":true}
  ]' \
  --alarm-actions <SCALE_TO_0_POLICY_ARN>

# Scale to 2 when queue has messages
aws cloudwatch put-metric-alarm \
  --alarm-name "alarm-for-sqs-fix-visible" \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --statistic Average \
  --period 60 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --dimensions Name=QueueName,Value=sqs-fix-transcribes \
  --alarm-actions <SCALE_TO_2_POLICY_ARN>
```

## Environment Variables

Bucket names and language are resolved per-message from DynamoDB — not from environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `MEDIA_TABLE` | `transcription-tracker` | DynamoDB table for per-entry bucket config |
| `SQS_QUEUE_URL` | — | Input queue URL (`sqs-fix-transcribes`) |
| `SQS_FINAL_QUEUE_URL` | — | Output notification queue (`sqs-final-transcribes`) |
| `WHISPER_MODEL` | `large` | stable-whisper model size |
| `WHISPER_CACHE` | `/opt/models/whisper` | Model cache directory |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `TOKEN_STEP` | `200` | Alignment granularity |
| `ROLLING_AVG_TARGET` | — | Target rolling average probability for quality evaluation |
| `DTW_ENABLED` | `true` | Enable pre-alignment DTW fix |
| `DTW_BAND_WIDTH` | `0` | Banded DTW (0 = no band) |
| `DTW_WINDOW_TYPE` | — | DTW window type |
| `DTW_STEP_PATTERN` | — | DTW step pattern |
| `DTW_MATCH_THRESHOLD` | `0.5` | Text match confidence threshold |
| `DTW_HIGH_DIST_THRESHOLD` | `0.7` | Distance threshold for mismatch detection |
| `DTW_LOW_SCORE_THRESHOLD` | — | Low score threshold for mismatch detection |
| `DTW_JUMP_THRESHOLD` | `40` | Max frame jump before flagging degradation |
| `DTW_DROP_THRESHOLD` | — | Probability drop threshold for truncation |
| `DTW_MA_WINDOW` | — | Moving average window size |

## Local Development

```bash
uv sync
uv run timestamp
```

## Algorithm Notes

- **Pre-alignment DTW**: Compares the old `.pre-fix.time` against the LLM-corrected text to find where the LLM may have hallucinated. Uses Levenshtein distance + DTW to align sequences and identify a cutoff point.
- **Post-alignment evaluation**: Uses rolling average of word probabilities (from stable-whisper output) and CUSUM to detect where timestamp quality degrades. Output is truncated at the degradation point.
- See `.claude/memory/dtw_replacements.md`, `dtw_step_patterns.md`, and `alignment_evaluation.md` for detailed algorithm documentation.
