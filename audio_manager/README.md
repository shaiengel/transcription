# Audio Manager

A CLI tool that queries today's Daf Yomi media links from an MSSQL database and downloads them.

## Requirements

- Python 3.12+
- uv package manager
- ODBC Driver 17 for SQL Server
- ffmpeg (for mp4 to mp3 conversion)

## Setup

1. Create a `.env` file with database credentials:

```env
DB_NAME=vps_daf-yomi
DB_HOST=127.0.0.1
DB_PORT=1433
DB_USER=readonly
DB_PASSWORD=your_password
DB_DRIVER_WINDOWS=ODBC Driver 17 for SQL Server
```

2. Install dependencies:

```bash
uv sync
```

3. Ensure ffmpeg is installed:

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

## Project Structure

```
src/audio_manager/
├── __init__.py
├── main.py                 # Entry point
├── models/
│   ├── __init__.py
│   └── schemas.py          # Pydantic models (CalendarEntry, MediaEntry)
├── handlers/
│   ├── __init__.py
│   └── media.py            # get_today_media_links(), print_media_links(), download_today_media()
└── services/
    ├── __init__.py
    ├── database.py         # Database connection and queries
    └── downloader.py       # File download and mp4→mp3 extraction
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
