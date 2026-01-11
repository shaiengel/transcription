# Audio Manager

A CLI tool that queries today's Daf Yomi media links from an MSSQL database.

## Requirements

- Python 3.12+
- uv package manager
- ODBC Driver 17 for SQL Server

## Setup

1. Create a `.env` file with database credentials:

```env
DB_ENGINE=mssql
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

## Usage

```bash
uv run audio-manager
```

This will:
1. Query the `Calendar` table for today's `MassechetId` and `DafId`
2. Fetch matching media links from the `View_Media` view
3. Print key fields: link, maggid, massechet, daf, language, duration

## Project Structure

```
src/audio_manager/
├── __init__.py
├── main.py                 # Entry point
├── handlers/
│   ├── __init__.py
│   └── media.py            # Media link printing logic
└── services/
    ├── __init__.py
    └── database.py         # Database connection and queries
```
