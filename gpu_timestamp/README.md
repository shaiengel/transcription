# GPU Timestamp Alignment Worker

GPU-based worker that re-aligns LLM-corrected transcriptions with original audio using stable-whisper.

## Overview

After the LLM reviewer fixes spelling/grammar in transcriptions, the timestamps may drift from the actual audio. This worker:
1. Downloads audio from `portal-daf-yomi-audio`
2. Downloads corrected text from `final-transcription`
3. Uses stable-whisper to align text with audio
4. Uploads new VTT and JSON files to `final-transcription`

## Usage

```bash
# Install dependencies
uv sync

# Run locally
uv run timestamp
```

## Docker

```bash
# Build
docker build -t gpu-timestamp .

# Run
docker run --gpus all gpu-timestamp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `AUDIO_BUCKET` | `portal-daf-yomi-audio` | Source bucket for audio |
| `TEXT_BUCKET` | `final-transcription` | Bucket for text files |
| `OUTPUT_BUCKET` | `final-transcription` | Output bucket for VTT |
| `SQS_QUEUE_URL` | - | SQS queue URL |
| `WHISPER_MODEL` | `base` | stable-whisper model name |
| `DEVICE` | `cuda` | Device (cuda or cpu) |
| `LANGUAGE` | `he` | Language code |
