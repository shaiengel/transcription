# CLAUDE.md - Architecture Guide for Claude Code

This file provides context and instructions for Claude Code to understand and work with the Audio Transcription Pipeline.

## Project Overview

This is an **event-driven, serverless audio transcription pipeline** on AWS. It processes MP3 files using a HuggingFace AST model on GPU, then refines transcriptions with AWS Bedrock (Claude) for spell-checking.

**Key Constraint:** The system must scale to zero when idle to minimize costs.

---

## Architecture Summary

```
On-Prem → S3 → Lambda → SQS → EC2 GPU → S3 → SQS → Lambda → Bedrock → S3 → SQS → On-Prem
```

### Component Chain

1. **On-Prem Service** uploads MP3s to S3 `/audio-input`
2. **Dispatcher Lambda** queues file paths to Audio Queue
3. **Auto Scaling Group** launches GPU Spot instance when queue has messages
4. **EC2 GPU Worker** transcribes files, saves to S3 `/transcriptions`, self-terminates
5. **LLM Caller Lambda** (concurrency: 3) calls Bedrock for spell-check
6. **Results Queue** notifies on-prem of completed files

---

## AWS Resources

### S3 Bucket Structure

```
s3://[BUCKET_NAME]/
├── audio-input/        # MP3 uploads from on-prem
├── transcriptions/     # Raw AST model output
└── final-output/       # Spell-checked final text
```

### SQS Queues

| Queue Name | Purpose | Visibility Timeout |
|------------|---------|-------------------|
| `audio-queue` | Files pending transcription | 10 minutes |
| `llm-queue` | Transcriptions pending spell-check | 5 minutes |
| `results-queue` | Completion notifications | 30 seconds |

### Lambda Functions

| Function | Trigger | Key Actions |
|----------|---------|-------------|
| `dispatcher-lambda` | S3 Event on `/audio-input` | Queue to `audio-queue` |
| `llm-caller-lambda` | SQS `llm-queue` | Call Bedrock, save to `/final-output`, notify `results-queue` |

### EC2 Auto Scaling Group

| Setting | Value | Reason |
|---------|-------|--------|
| Instance Type | `g4dn.xlarge` | NVIDIA T4 GPU for AST model |
| Market Type | Spot | ~70% cost savings |
| Min Capacity | 0 | Scale to zero when idle |
| Max Capacity | 1 | Single worker sufficient |
| Scale Trigger | CloudWatch Alarm | `ApproximateNumberOfMessagesVisible > 0` |

---

## Implementation Guidelines

### Dispatcher Lambda

**Runtime:** Python 3.12+
**Memory:** 256 MB
**Timeout:** 30 seconds

```python
# Key responsibilities:
# 1. Parse S3 event to get uploaded file info
# 2. Send message to audio-queue with file details
# 3. Do NOT start EC2 directly - ASG handles this via CloudWatch

import json
import boto3

sqs = boto3.client('sqs')
AUDIO_QUEUE_URL = 'https://sqs.[region].amazonaws.com/[account]/audio-queue'

def handler(event, context):
    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        
        message = {
            'bucket': bucket,
            'key': key,
            'timestamp': record['eventTime']
        }
        
        sqs.send_message(
            QueueUrl=AUDIO_QUEUE_URL,
            MessageBody=json.dumps(message)
        )
    
    return {'statusCode': 200}
```

### LLM Caller Lambda

**Runtime:** Python 3.11+
**Memory:** 512 MB
**Timeout:** 5 minutes
**Reserved Concurrency:** 3 (CRITICAL - prevents Bedrock rate limits)

```python
# Key responsibilities:
# 1. Receive transcription path from llm-queue
# 2. Download transcription from S3
# 3. Call Bedrock for spell-check
# 4. Save corrected text to /final-output
# 5. Send completion message to results-queue

import json
import boto3

s3 = boto3.client('s3')
sqs = boto3.client('sqs')
bedrock = boto3.client('bedrock-runtime')

RESULTS_QUEUE_URL = 'https://sqs.[region].amazonaws.com/[account]/results-queue'
BUCKET_NAME = '[BUCKET_NAME]'

def handler(event, context):
    for record in event['Records']:
        message = json.loads(record['body'])
        
        # Download transcription
        transcription = s3.get_object(
            Bucket=message['bucket'],
            Key=message['key']
        )['Body'].read().decode('utf-8')
        
        # Call Bedrock
        corrected = call_bedrock_spellcheck(transcription)
        
        # Save to final-output
        output_key = message['key'].replace('transcriptions/', 'final-output/')
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=output_key,
            Body=corrected.encode('utf-8')
        )
        
        # Notify results queue
        sqs.send_message(
            QueueUrl=RESULTS_QUEUE_URL,
            MessageBody=json.dumps({
                'source_file': message.get('source_audio', ''),
                'output_file': f's3://{BUCKET_NAME}/{output_key}',
                'status': 'success',
                'timestamp': datetime.utcnow().isoformat()
            })
        )

def call_bedrock_spellcheck(text):
    response = bedrock.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 4096,
            'messages': [{
                'role': 'user',
                'content': f'''Fix any spelling errors and transcription mistakes in this text. 
                Only return the corrected text, nothing else.
                
                Text:
                {text}'''
            }]
        })
    )
    
    result = json.loads(response['body'].read())
    return result['content'][0]['text']
```

### EC2 GPU Worker

**AMI Requirements:**
- Ubuntu 22.04 or Amazon Linux 2
- NVIDIA drivers + CUDA 11.8+
- Python 3.10+
- PyTorch with CUDA
- HuggingFace Transformers
- Pre-downloaded AST model

**Worker Script:** `/opt/worker/transcribe.py`

```python
# Key responsibilities:
# 1. Poll audio-queue for files
# 2. Download MP3 from S3
# 3. Transcribe with HuggingFace AST model
# 4. Upload transcription to /transcriptions
# 5. Send file info to llm-queue (via S3 event, not directly)
# 6. Self-terminate when queue empty for ~2 minutes

import boto3
import torch
from transformers import AutoModelForAudioClassification, AutoProcessor
import requests
import time

sqs = boto3.client('sqs')
s3 = boto3.client('s3')
ec2 = boto3.client('ec2')

AUDIO_QUEUE_URL = 'https://sqs.[region].amazonaws.com/[account]/audio-queue'
BUCKET_NAME = '[BUCKET_NAME]'

# Load model once at startup
model = AutoModelForAudioClassification.from_pretrained("MIT/ast-finetuned-audioset-10-10-0.4593")
processor = AutoProcessor.from_pretrained("MIT/ast-finetuned-audioset-10-10-0.4593")
model.to('cuda')

def main():
    empty_polls = 0
    
    while True:
        response = sqs.receive_message(
            QueueUrl=AUDIO_QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20  # Long polling
        )
        
        if 'Messages' in response:
            empty_polls = 0
            process_message(response['Messages'][0])
        else:
            empty_polls += 1
            if empty_polls >= 6:  # ~2 minutes of empty queue
                print("Queue empty, terminating...")
                terminate_self()
                return

def process_message(message):
    data = json.loads(message['Body'])
    
    # Download audio
    local_path = f"/tmp/{data['key'].split('/')[-1]}"
    s3.download_file(data['bucket'], data['key'], local_path)
    
    # Transcribe
    transcription = transcribe_audio(local_path)
    
    # Upload transcription
    output_key = data['key'].replace('audio-input/', 'transcriptions/').replace('.mp3', '.txt')
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=output_key,
        Body=transcription.encode('utf-8'),
        Metadata={'source_audio': data['key']}
    )
    
    # Delete message
    sqs.delete_message(
        QueueUrl=AUDIO_QUEUE_URL,
        ReceiptHandle=message['ReceiptHandle']
    )

def transcribe_audio(audio_path):
    # Implementation depends on actual AST model being used
    # This is a placeholder - actual implementation will vary
    # based on the specific HuggingFace model chosen
    pass

def terminate_self():
    instance_id = requests.get(
        'http://169.254.169.254/latest/meta-data/instance-id'
    ).text
    ec2.terminate_instances(InstanceIds=[instance_id])

if __name__ == '__main__':
    main()
```

---

## Infrastructure as Code

### Terraform Structure

```
terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── modules/
│   ├── s3/
│   │   └── main.tf
│   ├── sqs/
│   │   └── main.tf
│   ├── lambda/
│   │   ├── dispatcher/
│   │   └── llm-caller/
│   ├── ec2/
│   │   ├── asg.tf
│   │   ├── launch-template.tf
│   │   └── ami.tf
│   └── iam/
│       └── main.tf
└── environments/
    ├── dev.tfvars
    └── prod.tfvars
```

### Key Terraform Resources

```hcl
# Auto Scaling Group
resource "aws_autoscaling_group" "transcription_worker" {
  name                = "transcription-worker-asg"
  min_size            = 0
  max_size            = 1
  desired_capacity    = 0
  vpc_zone_identifier = var.subnet_ids

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Role"
    value               = "transcription-worker"
    propagate_at_launch = true
  }
}

# CloudWatch Alarm to scale up
resource "aws_cloudwatch_metric_alarm" "queue_not_empty" {
  alarm_name          = "audio-queue-has-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Average"
  threshold           = 0

  dimensions = {
    QueueName = aws_sqs_queue.audio_queue.name
  }

  alarm_actions = [aws_autoscaling_policy.scale_up.arn]
}

# Lambda reserved concurrency
resource "aws_lambda_function" "llm_caller" {
  function_name    = "llm-caller"
  reserved_concurrent_executions = 3  # CRITICAL
  # ... other config
}
```

---

## Important Design Decisions

### Why SQS between components?

1. **Lambda time limits**: Can't process all files in one Lambda invocation
2. **Concurrency control**: Prevents overwhelming Bedrock API
3. **Decoupling**: Each component can fail/retry independently
4. **Backpressure**: Natural rate limiting

### Why EC2 Spot instead of Lambda for transcription?

1. **GPU required**: Lambda doesn't support GPU
2. **Model loading**: AST model is large, Lambda cold starts would be expensive
3. **Spot pricing**: ~70% cheaper than on-demand

### Why self-termination instead of ASG scale-down?

1. **Faster**: Worker knows immediately when queue is empty
2. **Cost savings**: Don't wait for CloudWatch alarm cooldown
3. **Clean shutdown**: Can finish current file before terminating

### Why Reserved Concurrency on LLM Lambda?

1. **Bedrock rate limits**: Prevents throttling errors
2. **Cost control**: Limits parallel API calls
3. **Predictable**: Easier to estimate costs

---

## Common Tasks

### Adding a new file type

1. Update S3 event filter on Dispatcher Lambda
2. Update worker to handle new format
3. Test with sample files

### Changing the AST model

1. Update worker AMI with new model
2. Adjust instance type if needed (more/less GPU memory)
3. Update visibility timeout if processing time changes

### Increasing throughput

1. Increase ASG `max_size` for parallel GPU workers
2. Increase LLM Lambda concurrency (watch Bedrock limits)
3. Consider SQS FIFO for ordering if needed

### Debugging failed transcriptions

1. Check CloudWatch Logs for Lambda and EC2
2. Check DLQ for failed messages
3. Verify IAM permissions
4. Test with sample file locally

---

## Environment Variables

### Dispatcher Lambda

```
AUDIO_QUEUE_URL=https://sqs.[region].amazonaws.com/[account]/audio-queue
```

### LLM Caller Lambda

```
BUCKET_NAME=[bucket-name]
RESULTS_QUEUE_URL=https://sqs.[region].amazonaws.com/[account]/results-queue
BEDROCK_MODEL_ID=anthropic.claude-opus-4-5-20251101-v1:0
```

### EC2 Worker

```
AUDIO_QUEUE_URL=https://sqs.[region].amazonaws.com/[account]/audio-queue
BUCKET_NAME=[bucket-name]
AWS_REGION=[region]
```

---

## Testing

### Unit Tests

- Test Lambda handlers with mock S3/SQS events
- Test Bedrock prompt with sample transcriptions
- Test worker message processing logic

### Integration Tests

1. Upload test MP3 to S3
2. Verify message appears in audio-queue
3. Verify EC2 instance launches
4. Verify transcription appears in /transcriptions
5. Verify corrected text appears in /final-output
6. Verify completion message in results-queue

### Load Tests

1. Upload 100+ files simultaneously
2. Monitor ASG behavior
3. Monitor Lambda concurrency
4. Check for throttling errors

---

## Monitoring & Alerts

### CloudWatch Alarms

| Alarm | Condition | Action |
|-------|-----------|--------|
| DLQ has messages | Messages > 0 | SNS notification |
| Lambda errors | Errors > 5/min | SNS notification |
| ASG unhealthy | UnhealthyHosts > 0 | SNS notification |
| Queue age | OldestMessage > 1hr | SNS notification |

### Key Metrics to Dashboard

- `audio-queue` message count
- `llm-queue` message count
- Lambda invocations and errors
- EC2 instance count
- Bedrock token usage

---

## File Naming Conventions

```
Input:   audio-input/[original-filename].mp3
Trans:   transcriptions/[original-filename].txt
Output:  final-output/[original-filename].txt
```

The filename is preserved through the pipeline for traceability.

---

## Audio Manager (`audio_manager/`)

A CLI tool to fetch, display, download, upload, and publish today's Daf Yomi media links to S3 and SQS. Supports multiple media sources via abstract class pattern.

### Architecture

```
main.py → DependenciesContainer (DI)
        → MediaSource (abstract) ─┬→ DatabaseMediaSource → services/database.py → MSSQL
                                  └→ LocalDiskMediaSource → local filesystem
        → handlers/media.py → services/downloader.py → httpx/ffmpeg
                            → S3Uploader (injected) → S3Client → boto3 → S3
                            → SQSPublisher (injected) → SQSClient → boto3 → SQS
```

### Project Structure

```
audio_manager/
├── pyproject.toml
├── .env                        # Database/AWS credentials (not committed)
└── src/audio_manager/
    ├── __init__.py
    ├── main.py                 # Entry point, creates DI container, manages temp directory
    ├── models/
    │   ├── __init__.py
    │   ├── schemas.py          # Pydantic: CalendarEntry, MediaEntry
    │   └── media_source.py     # Abstract MediaSource class
    ├── handlers/
    │   ├── __init__.py
    │   └── media.py            # print_media_links(), download_today_media(), upload_media_to_s3(), publish_uploads_to_sqs()
    ├── infrastructure/
    │   ├── __init__.py
    │   ├── dependency_injection.py  # DependenciesContainer (DI container)
    │   ├── database_media_source.py # DatabaseMediaSource (fetches from MSSQL)
    │   ├── local_disk_media_source.py # LocalDiskMediaSource (reads from local dir)
    │   ├── s3_client.py        # S3Client class
    │   └── sqs_client.py       # SQSClient class
    └── services/
        ├── __init__.py
        ├── database.py         # DB connection, queries
        ├── downloader.py       # File download, mp4→mp3 extraction
        ├── s3_uploader.py      # S3Uploader class
        └── sqs_publisher.py    # SQSPublisher class
```

### Key Components

| File | Purpose |
|------|---------|
| `models/schemas.py` | Pydantic models: `CalendarEntry`, `MediaEntry` |
| `models/media_source.py` | Abstract `MediaSource` class |
| `handlers/media.py` | Print, download, upload, publish media |
| `infrastructure/dependency_injection.py` | DI container with singleton providers |
| `infrastructure/database_media_source.py` | `DatabaseMediaSource` - fetches from MSSQL |
| `infrastructure/local_disk_media_source.py` | `LocalDiskMediaSource` - reads from local directory |
| `infrastructure/s3_client.py` | S3Client wrapper for boto3 |
| `infrastructure/sqs_client.py` | SQSClient wrapper for boto3 |
| `services/database.py` | SQLAlchemy connection, queries |
| `services/downloader.py` | httpx download, ffmpeg extraction |
| `services/s3_uploader.py` | S3Uploader class (receives S3Client via DI) |
| `services/sqs_publisher.py` | SQSPublisher class (receives SQSClient via DI) |

### Media Source Selection

The media source is configured in `infrastructure/dependency_injection.py`. Comment/uncomment to switch:

```python
# In DependenciesContainer class:
media_source = providers.Singleton(_create_database_media_source)  # From MSSQL DB
# media_source = providers.Singleton(_create_local_disk_media_source)  # From local disk
```

### Database

- **Engine**: MSSQL via SQLAlchemy + pyodbc
- **Tables**:
  - `[vps_daf-yomi].[dbo].[Calendar]` - Maps dates to MassechetId/DafId
  - `[vps_daf-yomi].[dbo].[View_Media]` - Media links by massechet_id/daf_id

### Environment Variables (`.env`)

```
# Database (for DatabaseMediaSource)
DB_NAME=vps_daf-yomi
DB_HOST=127.0.0.1
DB_PORT=1433
DB_USER=readonly
DB_PASSWORD=xxx
DB_DRIVER_WINDOWS=ODBC Driver 17 for SQL Server

# Local Disk (for LocalDiskMediaSource)
LOCAL_MEDIA_DIR=./media
LOCAL_MEDIA_LANGUAGE=hebrew
LOCAL_DETAILS=Bava Kamma 2a

# AWS
AWS_PROFILE=transcription
S3_BUCKET=your-bucket-name
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/ACCOUNT/queue-name

# Language filter (comma-separated)
ALLOWED_LANGUAGES=hebrew
```

### AWS Configuration (`~/.aws/credentials`)

```ini
[default]
aws_access_key_id = YOUR_KEY
aws_secret_access_key = YOUR_SECRET

[transcription]
role_arn = arn:aws:iam::ACCOUNT:role/ROLE_NAME
source_profile = default
region = us-east-1
```

### Commands

```bash
cd audio_manager
uv sync              # Install dependencies
uv run audio-manager # Run CLI (fetches, prints, downloads, uploads to S3, publishes to SQS)
```

### Dependency Injection

Uses `dependency-injector` library. Container provides singletons:

```
media_source (DatabaseMediaSource or LocalDiskMediaSource)
session → s3_boto_client → s3_client → s3_uploader
        → sqs_boto_client → sqs_client → sqs_publisher
```

Usage in `main.py`:
```python
container = DependenciesContainer()

# Get media from configured source (see dependency_injection.py to switch)
media_source = container.media_source()
media_links = media_source.get_media_entries()

with tempfile.TemporaryDirectory(prefix="transcription_", delete=True, ignore_cleanup_errors=True) as temp_dir:
    download_dir = Path(temp_dir)
    download_today_media(media_links, download_dir)  # Skips if already local

    s3_uploader = container.s3_uploader()
    sqs_publisher = container.sqs_publisher()
    upload_media_to_s3(media_links, s3_uploader)
    publish_uploads_to_sqs(media_links, sqs_publisher)
# Temp directory auto-cleaned up here
```

### Adding New Features

1. **New Pydantic models**: Add to `models/schemas.py`
2. **New queries**: Add to `services/database.py`
3. **New handlers**: Create in `handlers/`
4. **New AWS clients**: Add provider to `infrastructure/dependency_injection.py`
5. **New CLI commands**: Extend `main.py`
6. **New media source**: Implement `MediaSource` abstract class in `infrastructure/`

---

## GPU Instance (`gpu_instance/`)

Dockerized GPU transcription worker using faster-whisper for Hebrew audio transcription.

### Architecture

```
SQS Queue → Docker Container → S3
              ├── SQSReceiver (polls messages)
              ├── S3Downloader (fetches audio)
              ├── WhisperModel (transcribes)
              ├── Formatters (VTT, TXT, TimedText)
              └── S3Uploader (saves transcriptions)
```

### Project Structure

```
gpu_instance/
├── Dockerfile              # CUDA 12.4 + Ubuntu 22.04 + Python 3.12
├── .dockerignore
├── pyproject.toml
├── .env
└── src/gpu_instance/
    ├── main.py             # Entry point
    ├── config.py           # Environment configuration
    ├── models/
    │   ├── schemas.py      # SQSMessage, TranscriptionResult
    │   └── formatter.py    # Abstract Formatter, SegmentData
    ├── handlers/
    │   └── transcription.py # Worker loop, message processing
    ├── services/
    │   ├── transcriber.py  # Whisper model loading/inference
    │   ├── s3_downloader.py
    │   ├── s3_uploader.py
    │   └── sqs_receiver.py
    └── infrastructure/
        ├── dependency_injection.py
        ├── s3_client.py
        ├── sqs_client.py
        ├── vtt_formatter.py
        ├── text_formatter.py
        └── timed_text_formatter.py
```

### Docker Build & Push

```bash
# Build
cd gpu_instance
docker build -t gpu-transcriber .

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com
docker tag gpu-transcriber:latest 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
docker push 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

### EC2 User Data (cloud-config)

```yaml
#cloud-config
runcmd:
  - cp -r /opt/models /opt/dlami/nvme/
  - aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com
  - docker pull 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
  - docker run -d --gpus all -v /opt/dlami/nvme/models:/opt/models:ro 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

**Critical Notes:**
- Use `#cloud-config` format (not `#!/bin/bash`)
- Use `-d` flag for detached mode (or cloud-init blocks forever)
- Copy model to NVMe for fast loading (EBS is slow)
- Run `sudo cloud-init clean` before creating AMI

### AMI Requirements

- Ubuntu 22.04 with NVIDIA drivers + CUDA 12.4
- Docker + NVIDIA Container Toolkit
- Whisper model at `/opt/models/models--ivrit-ai--whisper-large-v3-ct2/snapshots/<hash>/`

### IAM Permissions for EC2 Instance Profile

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"],
            "Resource": "arn:aws:ecr:us-east-1:707072965202:repository/portal-daf-yomi/whisper-transcribe"
        }
    ]
}
```

Plus S3 and SQS permissions for the transcription buckets/queues.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `SOURCE_BUCKET` | `portal-daf-yomi-audio` | S3 bucket for audio |
| `DEST_BUCKET` | `portal-daf-yomi-transcription` | S3 bucket for output |
| `SQS_QUEUE_URL` | - | SQS queue URL |
| `WHISPER_MODEL` | `/opt/models/...` | Model path |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `COMPUTE_TYPE` | `float16` | Quantization type |
| `LANGUAGE` | `he` | Language code |
| `BEAM_SIZE` | `5` | Beam search width |

### Credential Handling

Uses boto3 default credential chain (no assume_role):
- **EC2**: Instance profile credentials (automatic)
- **Local**: `~/.aws/credentials` file
