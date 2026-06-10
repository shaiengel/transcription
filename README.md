# Daf Yomi Transcription Pipeline

Event-driven, serverless pipeline that transcribes daily Daf Yomi audio recordings, corrects them with an LLM, re-aligns timestamps, and publishes the final VTT files to GitLab.

---

## Projects

| Project | Function | Runs On | AWS Trigger | README |
|---------|----------|---------|-------------|--------|
| [audio_manager](audio_manager/) | Fetches today's audio from DB, uploads MP3s to S3, publishes to SQS | On-prem / dev machine | Manual / daily cron | [README](audio_manager/README.md) |
| [gpu_instance](gpu_instance/) | Transcribes MP3s using faster-whisper on GPU | EC2 Spot `g4dn.xlarge` | SQS `audio-queue` → CloudWatch → ASG | [README](gpu_instance/README.md) |
| [transcription_reviewer](transcription_reviewer/) | Reviews and fixes transcriptions using LLM (Bedrock or Gemini) | AWS Lambda | ASG scaled to 0 → CloudWatch Alarm → EventBridge → Lambda | [README](transcription_reviewer/README.md) |
| [gpu_timestamp](gpu_timestamp/) | Re-aligns corrected text with original audio using stable-whisper | EC2 Spot GPU | SQS `sqs-fix-transcribes` → CloudWatch → ASG | [README](gpu_timestamp/README.md) |
| [transcribe_reader](transcribe_reader/) | Syncs final VTT files from S3 to GitLab | On-prem / dev machine | Manual (run after pipeline completes) | [README](transcribe_reader/README.md) |
| [.config](.config/) | Central config management — renders `.env` files from JSON templates | — | — | [README](.config/README.md) |

---

## End-to-End Pipeline

```
On-Prem / Dev
    │
    │  audio_manager: query DB, download audio, upload to S3, publish to SQS
    ▼
S3: portal-daf-yomi-audio/{media_id}.mp3
    │
    │  SQS: audio-queue  →  CloudWatch Alarm  →  ASG scale-up
    ▼
EC2 Spot (gpu_instance): faster-whisper transcription
    │
    │  Outputs: {stem}.txt, {stem}.vtt, {stem}.time
    ▼
S3: portal-daf-yomi-transcription/
    │
    │  ASG scaled to 0  →  CloudWatch Alarm  →  EventBridge  →  Lambda trigger
    │  (reserved concurrency = 1 ensures only one instance runs at a time,
    │   preventing duplicate processing of the same files)
    ▼
Lambda (transcription_reviewer): LLM spell-check + grammar fix
    │
    │  Publishes each fixed file to SQS: sqs-fix-transcribes
    │  (files are queued individually so gpu_timestamp ASG can scale and
    │   process in parallel without two workers picking the same file)
    │
    │  Backend A (Bedrock batch, async):
    │    └── Submits batch job; EventBridge fires on completion → writes S3 + SQS
    │
    │  Backend B (Gemini, sync):
    │    └── Writes directly to S3 + SQS notification
    ▼
S3: final-transcription/{stem}.txt + {stem}.pre-fix.time
    │
    │  SQS: sqs-fix-transcribes  →  CloudWatch Alarm  →  ASG scale-up
    ▼
EC2 Spot (gpu_timestamp): stable-whisper timestamp re-alignment
    │
    │  Outputs: {stem}.vtt, {stem}.srt, {stem}.json
    ▼
S3: final-transcription/{stem}.vtt
    │
    │  SQS: sqs-final-transcribes (completion notification)
    ▼
transcribe_reader: sync VTTs from S3 → GitLab
    ▼
GitLab: backend/data/portal_transcriptions/{media_id}.vtt
```

---

## SQS Queues

| Queue | From | To | Purpose |
|-------|------|----|---------|
| `audio-queue` | audio_manager | gpu_instance (via ASG) | MP3 files ready for transcription |
| `sqs-fix-transcribes` | transcription_reviewer | gpu_timestamp (via ASG) | Corrected text ready for timestamp alignment |
| `sqs-final-transcribes` | gpu_timestamp | on-prem consumer | Final VTT available in S3 |

---

## S3 Buckets

| Bucket | Contents | Written By | Read By |
|--------|----------|------------|---------|
| `portal-daf-yomi-audio` | MP3 audio + system prompt templates | audio_manager | gpu_instance, transcription_reviewer |
| `portal-daf-yomi-transcription` | Raw transcriptions (`.txt`, `.vtt`, `.time`) | gpu_instance | transcription_reviewer |
| `final-transcription` | LLM-fixed text + re-aligned VTTs | transcription_reviewer, gpu_timestamp | gpu_timestamp, transcribe_reader |
| `portal-daf-yomi-models` | Whisper model weights | (manual upload) | gpu_instance, gpu_timestamp |

---

## File Naming

Files keep the `{media_id}` (stem) throughout the pipeline for traceability:

```
portal-daf-yomi-audio/151415.mp3
portal-daf-yomi-transcription/151415.txt
portal-daf-yomi-transcription/151415.time
final-transcription/151415.txt          ← LLM-fixed
final-transcription/151415.pre-fix.time ← original before LLM
final-transcription/151415.vtt          ← final with corrected timestamps
```

---

## AWS Components Summary

| Component | Type | Purpose |
|-----------|------|---------|
| `portal-transcription-fix` ASG | EC2 Auto Scaling Group | Runs gpu_instance Spot workers (Min: 0, Max: 1) |
| `portal-timestamp-fix` ASG | EC2 Auto Scaling Group | Runs gpu_timestamp Spot workers (Min: 0, Max: 2) |
| `transcription-reviewer` | Lambda (concurrency: 1) | LLM spell-check — single instance prevents duplicate file processing |
| `portal-reviewer-role` | IAM Role | Lambda permissions for transcription_reviewer |
| `portal-bedrock-batch` | IAM Role | Bedrock batch job execution role |

---

## Cost (approx. 100 files/day)

| Component | Cost/day |
|-----------|----------|
| EC2 Spot GPU (transcription ~50 min) | ~$0.13 |
| EC2 Spot GPU (timestamp alignment) | ~$0.05 |
| Lambda invocations | ~$0.02 |
| Bedrock / Gemini LLM | ~$0.30 |
| S3 storage | ~$0.023/GB/month |
| **Total** | **~$0.50/day (~$15/month)** |

All compute scales to zero when idle.

---

## Design Decisions

### Why SQS between components?

1. **Lambda time limits**: Can't process all files in one Lambda invocation
2. **Concurrency control**: Prevents overwhelming Bedrock API
3. **Decoupling**: Each component can fail/retry independently
4. **Backpressure**: Natural rate limiting

### Why EC2 Spot instead of Lambda for transcription?

1. **GPU required**: Lambda doesn't support GPU
2. **Model loading**: Whisper model is large; Lambda cold starts would be expensive
3. **Spot pricing**: ~70% cheaper than on-demand

### Why self-termination instead of ASG scale-down?

1. **Faster**: Worker knows immediately when queue is empty
2. **Cost savings**: Don't wait for CloudWatch alarm cooldown
3. **Clean shutdown**: Can finish current file before terminating

### Why Reserved Concurrency = 1 on transcription_reviewer Lambda?

1. **No duplicate processing**: Only one Lambda instance reads S3 at a time, so the same file is never sent to the LLM twice
2. **Safe queue publishing**: Each fixed file is enqueued to `sqs-fix-transcribes` exactly once, letting gpu_timestamp workers scale freely without collision
3. **Bedrock rate limits**: Prevents parallel invocations from overwhelming the API
