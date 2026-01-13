# Audio Transcription Pipeline

An event-driven, serverless architecture for transcribing audio files using HuggingFace AST model with LLM-powered spell-checking.

## Overview

This system processes MP3 files uploaded from an on-premises service, transcribes them using a GPU-accelerated HuggingFace AST model, and refines the transcriptions using AWS Bedrock (Claude) for spell-checking and error correction.

### Key Features

- **Cost-optimized**: All compute scales to zero when idle
- **Event-driven**: No polling or always-on resources
- **Concurrency-controlled**: SQS queues prevent Lambda timeout and rate limit issues
- **Fault-tolerant**: Auto Scaling Group handles Spot interruptions
- **Feedback loop**: On-prem service receives completion notifications

---

## Architecture Diagram

```
┌─────────────────┐
│   ON-PREM       │
│   SERVICE       │
│  (Daily Cron)   │
└────────┬────────┘
         │
         │ 1. Upload MP3 files
         ▼
┌─────────────────┐
│   S3 Bucket     │
│   /audio-input  │
└────────┬────────┘
         │
         │ 2. S3 Event (PutObject)
         ▼
┌─────────────────┐
│   Lambda        │
│  (Dispatcher)   │
└────────┬────────┘
         │
         │ 3. Queue file path
         ▼
┌─────────────────┐       4. CloudWatch Alarm        ┌──────────────────┐
│   SQS Queue     │          (Messages > 0)          │  Auto Scaling    │
│  (Audio Queue)  │ ───────────────────────────────▶ │  Group           │
│                 │                                  │  Min: 0 Max: 1   │
└─────────────────┘                                  └────────┬─────────┘
        ▲                                                     │
        │                                          5. Launch Spot Instance
        │ 6. Poll                                             │
        │                                                     ▼
        │                        ┌─────────────────────────────────────────┐
        │                        │   EC2 Spot GPU Instance (g4dn.xlarge)   │
        └────────────────────────│                                         │
                                 │   • Poll SQS for files                  │
                                 │   • Download MP3 from S3                │
                                 │   • Transcribe with HuggingFace AST     │
                                 │   • Upload transcription to S3          │
                                 │   • Self-terminate when queue empty     │
                                 └──────────────────┬──────────────────────┘
                                                    │
                                                    │ 7. Save transcription
                                                    ▼
                                 ┌─────────────────────────────────────────┐
                                 │   S3 Bucket: /transcriptions            │
                                 └──────────────────┬──────────────────────┘
                                                    │
                                                    │ 8. S3 Event
                                                    ▼
                                 ┌─────────────────────────────────────────┐
                                 │   SQS Queue (LLM Queue)                 │
                                 └──────────────────┬──────────────────────┘
                                                    │
                                                    │ 9. Poll (concurrency: 3)
                                                    ▼
                                 ┌─────────────────────────────────────────┐
                                 │   Lambda (LLM Caller)                   │
                                 │   Reserved Concurrency: 3               │
                                 └──────────────────┬──────────────────────┘
                                                    │
                                                    │ 10. Call Bedrock
                                                    ▼
                                 ┌─────────────────────────────────────────┐
                                 │   AWS Bedrock (Claude)                  │
                                 │   • Spell-check                         │
                                 │   • Error correction                    │
                                 └──────────────────┬──────────────────────┘
                                                    │
                                          11. Save & notify
                                      ┌─────────────┴─────────────┐
                                      ▼                           ▼
                      ┌───────────────────────┐    ┌───────────────────────┐
                      │ S3: /final-output     │    │ SQS: Results Queue    │
                      └───────────────────────┘    └───────────┬───────────┘
                                                               │
                                                               │ 12. Poll results
                                                               ▼
                                                   ┌───────────────────────┐
                                                   │   ON-PREM SERVICE     │
                                                   └───────────────────────┘
```

---

## Components

### S3 Buckets

| Bucket/Prefix | Purpose |
|---------------|---------|
| `/audio-input` | MP3 files uploaded by on-prem service |
| `/transcriptions` | Raw transcriptions from AST model |
| `/final-output` | Spell-checked final transcriptions |

### SQS Queues

| Queue | Purpose | Settings |
|-------|---------|----------|
| Audio Queue | Buffer for files to transcribe | Visibility: 10 min |
| LLM Queue | Buffer for transcriptions to spell-check | Visibility: 5 min |
| Results Queue | Completion notifications for on-prem | Visibility: 30 sec |

### Lambda Functions

| Function | Trigger | Purpose | Concurrency |
|----------|---------|---------|-------------|
| Dispatcher | S3 Event (audio-input) | Queue files, ensure GPU is running | Default |
| LLM Caller | SQS (LLM Queue) | Call Bedrock for spell-check | Reserved: 3 |

### EC2 (Auto Scaling Group)

| Setting | Value |
|---------|-------|
| Instance Type | g4dn.xlarge (or g5.xlarge) |
| Market Type | Spot |
| Min Capacity | 0 |
| Max Capacity | 1 |
| Scale Up | CloudWatch Alarm (SQS Messages > 0) |
| Scale Down | Worker self-terminates |

### AWS Bedrock

| Setting | Value |
|---------|-------|
| Model | Claude (anthropic.claude-3-sonnet or claude-3-haiku) |
| Purpose | Spell-check and transcription error correction |

---

## Data Flow

### Step-by-Step Process

1. **On-prem service** uploads MP3 files to `s3://bucket/audio-input/`
2. **S3 Event** triggers **Dispatcher Lambda**
3. **Dispatcher Lambda** sends file path to **Audio Queue**
4. **CloudWatch Alarm** detects messages in queue, scales ASG to 1
5. **EC2 Spot Instance** launches with pre-configured AMI
6. **Worker process** polls Audio Queue:
   - Downloads MP3 from S3
   - Transcribes using HuggingFace AST model (GPU)
   - Uploads transcription to `/transcriptions`
   - Deletes SQS message
   - Repeats until queue is empty
   - Self-terminates
7. **S3 Event** on `/transcriptions` sends path to **LLM Queue**
8. **LLM Caller Lambda** (max 3 concurrent):
   - Downloads transcription from S3
   - Calls Bedrock for spell-check
   - Saves corrected text to `/final-output`
   - Sends completion message to **Results Queue**
9. **On-prem service** polls Results Queue for completed files

### Message Formats

**Audio Queue Message (from audio_manager to gpu_instance):**
```json
{
  "s3_key": "recording-001.mp3",
  "language": "hebrew",
  "details": "Bava Kamma 2a"
}
```

**LLM Queue Message (from gpu_instance to LLM Lambda):**
```json
{
  "timed_key": "transcriptions/recording-001.timed.txt",
  "details": "Bava Kamma 2a",
  "language": "hebrew"
}
```

**Results Queue Message:**
```json
{
  "source_file": "audio-input/recording-001.mp3",
  "output_file": "s3://my-bucket/final-output/recording-001.txt",
  "status": "success",
  "timestamp": "2025-01-04T10:06:00Z"
}
```

---

## Cost Breakdown

| Component | When Active | When Idle |
|-----------|-------------|-----------|
| EC2 Spot GPU (g4dn.xlarge) | ~$0.16/hr | $0 (terminated) |
| Lambda (Dispatcher) | ~$0.0001/invoke | $0 |
| Lambda (LLM Caller) | ~$0.0001/invoke | $0 |
| SQS | ~$0.40/million requests | $0 |
| Bedrock Claude | ~$0.003/1K tokens | $0 |
| S3 Storage | - | ~$0.023/GB/month |

**Example: 100 files/day, 30 sec audio each**
- GPU time: ~50 min/day → ~$0.13/day
- Lambda: ~200 invocations → ~$0.02/day
- Bedrock: ~100K tokens → ~$0.30/day
- **Total: ~$0.45/day or ~$14/month**

---

## Deployment

### Prerequisites

- AWS Account with appropriate permissions
- Terraform or AWS CDK installed
- Docker (for building worker AMI)

### Infrastructure Components to Deploy

1. S3 Bucket with three prefixes
2. Three SQS Queues (Audio, LLM, Results)
3. Two Lambda Functions (Dispatcher, LLM Caller)
4. Auto Scaling Group with Launch Template
5. CloudWatch Alarm for scaling
6. IAM Roles and Policies
7. EC2 AMI with pre-installed dependencies

### EC2 AMI Requirements

The GPU instance AMI should include:
- Ubuntu 22.04 or Amazon Linux 2
- NVIDIA drivers + CUDA 11.8+
- Python 3.10+
- PyTorch with CUDA support
- HuggingFace Transformers
- Pre-downloaded AST model weights
- Worker script configured as systemd service

---

## Error Handling

### Dead Letter Queues

Each SQS queue should have an associated DLQ:
- `audio-queue-dlq`: Failed transcription attempts
- `llm-queue-dlq`: Failed LLM processing attempts

### Retry Policy

| Queue | Max Receives | Then |
|-------|--------------|------|
| Audio Queue | 3 | Move to DLQ |
| LLM Queue | 3 | Move to DLQ |

### Monitoring

Set up CloudWatch alarms for:
- DLQ message count > 0
- Lambda errors
- ASG instance health

---

## Security

### IAM Roles

**Dispatcher Lambda:**
- `s3:GetObject` on audio-input
- `sqs:SendMessage` on Audio Queue
- `ec2:DescribeInstances`
- `autoscaling:SetDesiredCapacity`

**LLM Caller Lambda:**
- `s3:GetObject` on transcriptions
- `s3:PutObject` on final-output
- `sqs:*` on LLM Queue and Results Queue
- `bedrock:InvokeModel`

**EC2 Worker:**
- `s3:GetObject` on audio-input
- `s3:PutObject` on transcriptions
- `sqs:*` on Audio Queue
- `ec2:TerminateInstances` (self only)

### Encryption

- S3: SSE-S3 or SSE-KMS
- SQS: SSE enabled
- Data in transit: TLS

---

## Future Improvements

1. **DynamoDB tracking table** for full job visibility
2. **SNS notifications** for errors/completions
3. **Step Functions** for complex orchestration
4. **Batch processing** for very high volumes
5. **Multi-region** for disaster recovery
