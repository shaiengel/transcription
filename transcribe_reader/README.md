# Transcribe Reader

CLI tool to sync VTT transcription files from S3 to GitLab. Reads today's media IDs from the database, fetches corresponding VTT files from S3, and uploads them to a GitLab repository.

## Architecture

```
Database (Calendar + View_Media)
         ↓
    Get today's media_ids
         ↓
S3 (portal-daf-yomi-transcription)
         ↓
    Download {media_id}.vtt files
         ↓
GitLab API (python-gitlab)
         ↓
    Upload to backend/data/portal_transcriptions/
```

## Project Structure

```
transcribe_reader/
├── pyproject.toml
├── .env                        # Database/AWS/GitLab credentials
└── src/transcribe_reader/
    ├── __init__.py
    ├── main.py                 # Entry point
    ├── models/
    │   ├── __init__.py
    │   └── schemas.py          # CalendarEntry, MediaInfo, VttFile
    ├── handlers/
    │   ├── __init__.py
    │   └── sync.py             # Main sync orchestration
    ├── services/
    │   ├── __init__.py
    │   ├── database.py         # DB connection, queries
    │   ├── s3_downloader.py    # S3Downloader class
    │   └── gitlab_uploader.py  # GitLabUploader class
    └── infrastructure/
        ├── __init__.py
        ├── dependency_injection.py
        ├── s3_client.py        # S3Client wrapper
        └── gitlab_client.py    # GitLabClient wrapper
```

## Setup

### 1. Install Dependencies

```bash
cd transcribe_reader
uv sync
```

### 2. Create GitLab Access Token

1. Go to GitLab > User Settings > Access Tokens
2. Create token with scopes: `api`, `write_repository`
3. Copy the token

### 3. Configure Environment

Edit `.env` file:

```bash
# Database (same as audio_manager)
DB_NAME=vps_daf-yomi
DB_HOST=127.0.0.1
DB_PORT=1433
DB_USER=readonly
DB_PASSWORD=xxx
DB_DRIVER_WINDOWS=ODBC Driver 17 for SQL Server

# AWS
AWS_PROFILE=default
S3_TRANSCRIPTION_BUCKET=portal-daf-yomi-transcription

# GitLab
GITLAB_URL=https://gitlab.com
GITLAB_PROJECT_ID=llm241203/dy6
GITLAB_PRIVATE_TOKEN=glpat-xxxxxxxxxxxx
GITLAB_BRANCH=main
```

## Usage

```bash
uv run transcribe-reader
```

### Output

```
2024-01-23 12:00:00 - INFO - Starting transcription sync
2024-01-23 12:00:00 - INFO - Found 1 calendar entries for today
2024-01-23 12:00:00 - INFO -   Massechet 5, Daf 42: 15 media entries
2024-01-23 12:00:01 - INFO - Looking for 15 VTT files
2024-01-23 12:00:02 - INFO - Found in S3: 12345678.vtt
2024-01-23 12:00:02 - INFO - S3 check complete: 10/15 files available
2024-01-23 12:00:03 - INFO - Downloaded 10/10 files
2024-01-23 12:00:05 - INFO - Batch commit successful: 10 files
==================================================
Sync complete:
  Media entries found: 15
  VTT files in S3: 10
  Downloaded: 10
  Uploaded to GitLab: 10
```

## How It Works

1. **Query Database**: Gets today's `MassechetId` and `DafId` from `Calendar` table
2. **Get Media IDs**: Queries `View_Media` for all `media_id` values for today's entries
3. **Check S3**: Looks for `{media_id}.vtt` files in the transcription bucket
4. **Download**: Downloads VTT content from S3 into memory
5. **Upload to GitLab**: Batch commits all files to `backend/data/portal_transcriptions/`

## Dependencies

- `sqlalchemy` + `pyodbc` - Database access
- `boto3` - AWS S3 access
- `python-gitlab` - GitLab API
- `pydantic` - Data validation
- `dependency-injector` - DI container
- `python-dotenv` - Environment configuration
