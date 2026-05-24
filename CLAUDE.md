
# CLAUDE.md - Architecture Guide for Claude Code

This file provides context and instructions for Claude Code to understand and work with the Audio Transcription Pipeline.

## Project Overview

This is an **event-driven, serverless audio transcription pipeline** on AWS. It processes Hebrew MP3 files using faster-whisper (CTranslate2) on GPU, corrects transcriptions with an LLM (AWS Bedrock or Gemini), re-aligns timestamps using stable-whisper, and publishes final VTT files to GitLab.

**Key Constraint:** The system must scale to zero when idle to minimize costs.

---

## Architecture Summary

```
On-Prem → S3 → SQS → EC2 GPU (faster-whisper) → S3 → Lambda (LLM fix) → SQS → EC2 GPU (stable-whisper) → S3 → On-Prem → GitLab
```

### Component Chain

1. **audio_manager** (on-prem) — fetches media from DB or other source, uploads MP3s to S3, publishes to `audio-queue`
2. **gpu_instance** (EC2 Spot, ASG `portal-transcription-fix`) — transcribes audio with faster-whisper, outputs `.txt`/`.vtt`/`.time` to S3
3. **transcription_reviewer** (Lambda) — triggered when ASG scales to 0; fixes transcriptions with LLM (Bedrock batch or Gemini)
4. **gpu_timestamp** (EC2 Spot, ASG `portal-timestamp-fix`) — re-aligns corrected text with audio using stable-whisper, outputs final VTT
5. **transcribe_reader** (on-prem) — syncs final VTTs from S3 to GitLab

---

## AWS Resources

### SQS Queues

| Queue | From | To | Purpose |
|-------|------|----|---------|
| `audio-queue` | audio_manager | gpu_instance (via ASG) | MP3 files ready for transcription |
| `sqs-fix-transcribes` | transcription_reviewer | gpu_timestamp (via ASG) | Corrected text ready for timestamp alignment |
| `sqs-final-transcribes` | gpu_timestamp | on-prem consumer | Final VTT available in S3 |

### Lambda Functions

| Function | Trigger | Key Actions |
|----------|---------|-------------|
| `transcription-reviewer` | CloudWatch Alarm (ASG `portal-transcription-fix` scaled to 0) | List .txt files, fetch per-file system prompts, invoke LLM pipeline |

### EC2 Auto Scaling Groups

| ASG | Instance | Min/Max | Trigger |
|-----|----------|---------|---------|
| `portal-transcription-fix` | `g4dn.xlarge` Spot | 0/1 | `audio-queue` messages visible > 0 |
| `portal-timestamp-fix` | GPU Spot | 0/2 | `sqs-fix-transcribes` messages visible > 0 |

---

## Component Documentation

When working on a specific component, read its README for full implementation details, environment variables, project structure, and commands.

| Component | README | Key Responsibility |
|-----------|--------|-------------------|
| `audio_manager/` | [audio_manager/README.md](audio_manager/README.md) | Fetch media from DB, upload MP3s to S3, publish to SQS |
| `gpu_instance/` | [gpu_instance/README.md](gpu_instance/README.md) | Transcribe audio with faster-whisper on EC2 GPU |
| `transcription_reviewer/` | [transcription_reviewer/README.md](transcription_reviewer/README.md) | Fix transcriptions with LLM (Bedrock batch or Gemini) |
| `gpu_timestamp/` | [gpu_timestamp/README.md](gpu_timestamp/README.md) | Re-align corrected text with audio using stable-whisper |
| `transcribe_reader/` | [transcribe_reader/README.md](transcribe_reader/README.md) | Sync final VTTs from S3 to GitLab |
| `.config/` | [.config/README.md](.config/README.md) | Central config — render `.env` files from JSON templates |

---

## File Naming Conventions

Files keep the `{media_id}` (stem) throughout the pipeline for traceability:

```
portal-daf-yomi-audio/151415.mp3
portal-daf-yomi-transcription/151415.txt
portal-daf-yomi-transcription/151415.time
final-transcription/151415.txt          ← LLM-fixed
final-transcription/151415.pre-fix.time ← original before LLM
final-transcription/151415.vtt          ← final with corrected timestamps
```
