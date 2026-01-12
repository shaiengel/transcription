# Audio Manager

A CLI tool that queries today's Daf Yomi media links from an MSSQL database, downloads them, and uploads to S3.

## Requirements

- Python 3.12+
- uv package manager
- ODBC Driver 17 for SQL Server
- ffmpeg (for mp4 to mp3 conversion)

## Setup

1. Create a `.env` file with database and AWS credentials:

```env
DB_NAME=vps_daf-yomi
DB_HOST=127.0.0.1
DB_PORT=1433
DB_USER=readonly
DB_PASSWORD=your_password
DB_DRIVER_WINDOWS=ODBC Driver 17 for SQL Server

# AWS S3 Upload
AWS_PROFILE=transcription
S3_BUCKET=your-bucket-name
```

2. Configure AWS credentials in `~/.aws/credentials`:

```ini
[default]
aws_access_key_id = YOUR_KEY
aws_secret_access_key = YOUR_SECRET

[transcription]
role_arn = arn:aws:iam::ACCOUNT:role/ROLE_NAME
source_profile = default
region = us-east-1
```

3. Install dependencies:

```bash
uv sync
```

4. Ensure ffmpeg is installed:

```bash
ffmpeg -version
```

## Usage

```bash
uv run audio-manager
```

This will:
1. Query the `Calendar` table for today's `MassechetId` and `DafId`
2. Fetch matching media links from the `View_Media` view
3. Print summary statistics (count, duration by language/file type)
4. Download all media files to a temp directory
5. Convert mp4 files to mp3 using ffmpeg
6. Upload files to S3 bucket

## Project Structure

```
src/audio_manager/
├── __init__.py
├── main.py                 # Entry point, creates DI container
├── models/
│   ├── __init__.py
│   └── schemas.py          # Pydantic models (CalendarEntry, MediaEntry)
├── handlers/
│   ├── __init__.py
│   └── media.py            # get_today_media_links(), print_media_links(), download_today_media(), upload_media_to_s3()
├── infrastructure/
│   ├── __init__.py
│   ├── dependency_injection.py  # DependenciesContainer (DI container)
│   └── s3_client.py        # S3Client class
└── services/
    ├── __init__.py
    ├── database.py         # Database connection and queries
    ├── downloader.py       # File download and mp4→mp3 extraction
    └── s3_uploader.py      # S3Uploader class
```

## Dependency Injection

Uses `dependency-injector` library. The DI container provides singletons:

```
session → s3_boto_client → s3_client → s3_uploader
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
