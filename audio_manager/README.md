# Audio Manager

CLI tool that fetches today's Daf Yomi media from an MSSQL database (or local disk), downloads audio files, uploads them to S3, and publishes SQS messages to kick off transcription.

## Pipeline Position

```
On-Prem DB → audio_manager → [portal-daf-yomi-audio S3] → [audio-queue SQS] → gpu_instance
```

## AWS Trigger

**Manual / daily cron** — not event-driven. Run once per day (typically via a scheduled task or manually) after the day's recordings are available in the database.

- Runs on: developer machine or on-prem server
- Outputs to: `s3://portal-daf-yomi-audio/` and `audio-queue` SQS

## Architecture

```
main.py → DependenciesContainer (DI)
        → MediaSource (abstract) ─┬→ DatabaseMediaSource → services/database.py → MSSQL
                                  └→ LocalDiskMediaSource → local filesystem
        → handlers/media.py → services/downloader.py → httpx/ffmpeg
                            → S3Uploader (injected) → S3Client → boto3 → S3
                            → SQSPublisher (injected) → SQSClient → boto3 → SQS
```

## Project Structure

```
audio_manager/
├── pyproject.toml
├── .env                        # Database/AWS credentials (not committed)
└── src/audio_manager/
    ├── main.py                 # Entry point, creates DI container, manages temp directory
    ├── models/
    │   ├── schemas.py          # Pydantic: CalendarEntry, MediaEntry
    │   └── media_source.py     # Abstract MediaSource class
    ├── handlers/
    │   └── media.py            # print_media_links(), download_media(), upload_media_to_s3(), publish_uploads_to_sqs()
    ├── infrastructure/
    │   ├── dependency_injection.py  # DependenciesContainer (DI container)
    │   ├── database_media_source.py # Fetches from MSSQL
    │   ├── local_disk_media_source.py # Reads from local directory
    │   ├── s3_client.py        # S3Client wrapper
    │   └── sqs_client.py       # SQSClient wrapper
    └── services/
        ├── database.py         # SQLAlchemy connection, queries
        ├── downloader.py       # File download, mp4→mp3 extraction
        ├── s3_uploader.py      # S3Uploader class
        └── sqs_publisher.py    # SQSPublisher class
```

## Media Source Selection

Configured in `infrastructure/dependency_injection.py` (comment/uncomment):

```python
# Option 1: From MSSQL database (production)
media_source = providers.Singleton(_create_database_media_source)

# Option 2: From local disk (testing)
# media_source = providers.Singleton(_create_local_disk_media_source)
```

### Database Tables

| Table | Purpose |
|-------|---------|
| `[vps_daf-yomi].[dbo].[Calendar]` | Maps today's date → `MassechetId` / `DafId` |
| `[vps_daf-yomi].[dbo].[View_Media]` | Media links keyed by `massechet_id` / `daf_id` |

### Local Disk Source

Set `LOCAL_MEDIA_DIR` to a directory containing MP3 files. Set `LOCAL_DETAILS` for metadata.

## Requirements

- Python 3.12+
- `uv` package manager
- ODBC Driver 17 for SQL Server
- `ffmpeg` (for mp4 → mp3 conversion)

## Setup

1. Create `.env` with database and AWS credentials (see Environment Variables below)

2. Configure AWS credentials in `~/.aws/credentials`:
   ```ini
   [portal]
   aws_access_key_id = YOUR_KEY
   aws_secret_access_key = YOUR_SECRET

   [transcription]
   role_arn = arn:aws:iam::ACCOUNT:role/ROLE_NAME
   source_profile = portal
   region = us-east-1
   ```

3. Install dependencies:
   ```bash
   uv sync
   ```

## Usage

```bash
uv run audio-manager
```

This will:
1. Query `Calendar` for today's `MassechetId` and `DafId`
2. Fetch matching media links from `View_Media`
3. Print summary statistics (count, duration by language/file type)
4. Download all media files to a temp directory
5. Convert mp4 files to mp3 using ffmpeg
6. Upload files to S3 (filtered by `ALLOWED_LANGUAGES`)
7. Publish messages to SQS (filtered by `ALLOWED_LANGUAGES`)
8. Auto-cleanup temp directory on exit

## SQS Message Format

```json
{
  "s3_key": "123456.mp3",
  "language": "hebrew",
  "massechet_name": "Bava Kamma",
  "daf_name": "20"
}
```

## Environment Variables

```env
# Database (for DatabaseMediaSource)
DB_NAME=vps_daf-yomi
DB_HOST=127.0.0.1
DB_PORT=1433
DB_USER=readonly
DB_PASSWORD=your_password
DB_DRIVER_WINDOWS=ODBC Driver 17 for SQL Server

# Local Disk (for LocalDiskMediaSource)
LOCAL_MEDIA_DIR=./media
LOCAL_MEDIA_LANGUAGE=hebrew
LOCAL_DETAILS=Bava Kamma 2a

# AWS
AWS_PROFILE=transcription
S3_BUCKET=portal-daf-yomi-audio
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/ACCOUNT/audio-queue

# Language filter (comma-separated)
ALLOWED_LANGUAGES=hebrew
```

## Dependency Injection

Uses `dependency-injector` library. Container provides singletons:

```
media_source (DatabaseMediaSource or LocalDiskMediaSource)
session → s3_boto_client → s3_client → s3_uploader
        → sqs_boto_client → sqs_client → sqs_publisher
```

## Data Models

### CalendarEntry
- `massechet_id`: int
- `daf_id`: int

### MediaEntry
- `media_id`: int
- `media_link`: str
- `maggid_description`: str | None
- `massechet_name`: str
- `daf_name`: str
- `language`: str | None
- `media_duration`: int | None
- `file_type`: str | None
- `downloaded_path`: Path | None (set after download)
