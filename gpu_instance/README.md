# GPU Instance - Whisper Transcription Worker

A Dockerized GPU transcription worker using faster-whisper for Hebrew audio transcription.

## Docker Build

```bash
cd gpu_instance
docker build -t gpu-transcriber .
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

## Model Path

The Whisper model must be mounted at the path specified by `WHISPER_MODEL` env var. For HuggingFace cached models, mount the snapshot directory:

```
/path/to/models--ivrit-ai--whisper-large-v3-ct2/snapshots/<hash>/
```

## Push to ECR

```bash
# Authenticate
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag gpu-transcriber:latest 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
docker push 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

## EC2 Deployment

### AMI Requirements

- Ubuntu 22.04
- NVIDIA drivers + CUDA 12.4
- Docker installed
- NVIDIA Container Toolkit installed
- Whisper model pre-cached at `/opt/models/`
- Run `sudo cloud-init clean` before creating AMI

### IAM Role Permissions

The EC2 instance profile needs:
- `ecr:GetAuthorizationToken` (Resource: `*`)
- `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage` (Resource: ECR repo ARN)
- S3 read/write for audio and transcription buckets
- SQS send/receive/delete for transcription queue

### User Data (cloud-config)

```yaml
#cloud-config
runcmd:
  - cp -r /opt/models /opt/dlami/nvme/
  - aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com
  - docker pull 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
  - docker run -d --gpus all -v /opt/dlami/nvme/models:/opt/models:ro 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:1
```

**Important:** Use `-d` flag to run container in detached mode so cloud-init completes.

### Performance Optimization

Copy model from EBS to NVMe instance storage for faster loading:
- EBS: slow (~8 min to load 3GB model)
- NVMe (`/opt/dlami/nvme`): fast (~30 sec)

Note: NVMe is ephemeral - data lost on stop. Copy on each boot via user_data.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `SOURCE_BUCKET` | `portal-daf-yomi-audio` | S3 bucket for audio input |
| `DEST_BUCKET` | `portal-daf-yomi-transcription` | S3 bucket for transcriptions |
| `SQS_QUEUE_URL` | - | SQS queue URL for messages |
| `WHISPER_MODEL` | `/opt/models/...` | Path to Whisper model |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `COMPUTE_TYPE` | `float16` | `float16`, `int8`, `int8_float16` |
| `LANGUAGE` | `he` | Language code |
| `BEAM_SIZE` | `5` | Beam search width |

## AWS Credentials

The container uses boto3's default credential chain:
- On EC2: Instance profile (automatic)
- Locally: `~/.aws/credentials` (mount with `-v`)
