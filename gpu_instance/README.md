# GPU Instance — Whisper Transcription Worker

Dockerized GPU worker that transcribes Hebrew audio files using faster-whisper (CTranslate2). Runs on an EC2 Spot instance launched automatically when audio files are queued.

## Pipeline Position

```
audio_manager → [audio-queue SQS] → gpu_instance → [portal-daf-yomi-transcription S3] → transcription_reviewer
```

## AWS Trigger

**CloudWatch Alarm** on `audio-queue` SQS (`ApproximateNumberOfMessagesVisible > 0`) triggers the `portal-transcription-fix` Auto Scaling Group to launch an EC2 Spot instance. The container starts automatically via EC2 user data.

- Trigger: SQS `audio-queue` has messages → CloudWatch Alarm → ASG scale-up
- Runs on: EC2 `g4dn.xlarge` Spot (NVIDIA T4 GPU)
- ASG: `portal-transcription-fix` (Min: 0, Max: 1)
- Terminates: self-terminates after ~2 minutes of empty queue

## Architecture

```
SQS (audio-queue) → Docker Container → S3 (portal-daf-yomi-transcription)
                      ├── SQSReceiver (polls messages)
                      ├── S3Downloader (fetches audio)
                      ├── WhisperModel (faster-whisper transcription)
                      ├── Formatters (VTT, TXT, TimedText)
                      └── S3Uploader (saves transcriptions)
```

## Project Structure

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

## Output Files

For each audio file `{stem}.mp3`, written to `s3://portal-daf-yomi-transcription/`:

| File | Description |
|------|-------------|
| `{stem}.txt` | Plain text transcription |
| `{stem}.vtt` | VTT subtitles with timestamps |
| `{stem}.time` | Timed text (one line per segment with timestamp metadata) |

## Docker Build & Push

```bash
# Build
cd gpu_instance
docker build -t gpu-transcriber .

# Authenticate to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag gpu-transcriber:latest 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
docker push 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

## Local Testing

```bash
# With GPU
docker run --gpus all \
  -v /path/to/models:/opt/models:ro \
  -v ~/.aws:/root/.aws:ro \
  -e COMPUTE_TYPE=float16 \
  -e BEAM_SIZE=5 \
  gpu-transcriber

# Without GPU (CPU mode)
docker run \
  -v /path/to/models:/opt/models:ro \
  -v ~/.aws:/root/.aws:ro \
  -e DEVICE=cpu \
  -e COMPUTE_TYPE=int8 \
  -e BEAM_SIZE=5 \
  gpu-transcriber
```

## EC2 Deployment

### AMI Requirements

- Ubuntu 22.04
- NVIDIA drivers + CUDA 12.4
- Docker + NVIDIA Container Toolkit
- Whisper model pre-cached at `/opt/models/`
- Run `sudo cloud-init clean` before creating AMI

### User Data (cloud-config)

```yaml
#cloud-config
runcmd:
  - cp -r /opt/models /opt/dlami/nvme/
  - aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com
  - docker pull 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
  - docker run -d --gpus all -v /opt/dlami/nvme/models:/opt/models:ro 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

Use `-d` (detached) so cloud-init completes. Copy model to NVMe before running — EBS loads the 3 GB model in ~8 min, NVMe in ~30 sec. NVMe is ephemeral; copy on every boot via user data.

### Model Path

The Whisper model must be at the path specified by `WHISPER_MODEL`. For HuggingFace cached models:
```
/opt/models/models--ivrit-ai--whisper-large-v3-ct2/snapshots/<hash>/
```

### IAM Role Permissions

| Permission | Resource |
|------------|----------|
| `ecr:GetAuthorizationToken` | `*` |
| `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage` | ECR repo ARN |
| `s3:GetObject` | `portal-daf-yomi-audio` |
| `s3:PutObject` | `portal-daf-yomi-transcription` |
| `sqs:ReceiveMessage`, `sqs:DeleteMessage` | `audio-queue` |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `SOURCE_BUCKET` | `portal-daf-yomi-audio` | S3 bucket for audio input |
| `DEST_BUCKET` | `portal-daf-yomi-transcription` | S3 bucket for transcriptions |
| `SQS_QUEUE_URL` | — | SQS queue URL (`audio-queue`) |
| `WHISPER_MODEL` | `/opt/models/...` | Path to Whisper CTranslate2 model |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `COMPUTE_TYPE` | `float16` | `float16`, `int8`, `int8_float16` |
| `LANGUAGE` | `he` | Language code |
| `BEAM_SIZE` | `5` | Beam search width |

## AWS Credentials

Uses boto3 default credential chain:
- On EC2: Instance profile (automatic)
- Locally: `~/.aws/credentials` (mount with `-v ~/.aws:/root/.aws:ro`)
