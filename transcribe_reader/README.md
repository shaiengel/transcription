# Transcribe Reader

Sync VTT transcription files from S3 to GitLab.

## Usage

```bash
uv sync
uv run transcribe-reader
```

## Configuration

Copy `.env.example` to `.env` and configure:
- Database credentials (same as audio_manager)
- AWS profile for S3 access
- GitLab project ID and access token
