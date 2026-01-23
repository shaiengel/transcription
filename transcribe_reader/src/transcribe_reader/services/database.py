"""Database connection and queries."""

import os
from contextlib import contextmanager
from datetime import date
from typing import Generator
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine

from transcribe_reader.models.schemas import CalendarEntry, MediaInfo

# Module-level engine singleton
_engine: Engine | None = None


def _get_connection_string() -> str:
    """Build MSSQL connection string from environment variables."""
    load_dotenv()
    driver = os.getenv("DB_DRIVER_WINDOWS", "ODBC Driver 17 for SQL Server")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "1433")
    database = os.getenv("DB_NAME", "vps_daf-yomi")
    user = os.getenv("DB_USER", "readonly")
    password = os.getenv("DB_PASSWORD", "")

    encoded_password = quote_plus(password)
    encoded_driver = quote_plus(driver)

    return f"mssql+pyodbc://{user}:{encoded_password}@{host}:{port}/{database}?driver={encoded_driver}"


def get_engine() -> Engine:
    """Get or create the singleton engine."""
    global _engine
    if _engine is None:
        _engine = create_engine(_get_connection_string())
    return _engine


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Get a database connection from the pool."""
    engine = get_engine()
    with engine.connect() as conn:
        yield conn


def get_today_calendar_entries(conn: Connection) -> list[CalendarEntry]:
    """Get today's MassechetId and DafId from Calendar table."""
    today = date.today().isoformat()

    query = text("""
        SELECT DISTINCT MassechetId, DafId
        FROM [vps_daf-yomi].[dbo].[Calendar]
        WHERE Date = :today
    """)
    result = conn.execute(query, {"today": today}).fetchall()
    return [
        CalendarEntry(massechet_id=row[0], daf_id=row[1])
        for row in result
    ]


def get_media_ids(conn: Connection, massechet_id: int, daf_id: int) -> list[MediaInfo]:
    """Get media_ids for a specific massechet and daf."""
    query = text("""
        SELECT media_id, massechet_name, daf_name
        FROM [vps_daf-yomi].[dbo].[View_Media]
        WHERE massechet_id = :massechet_id AND daf_id = :daf_id
    """)
    result = conn.execute(
        query, {"massechet_id": massechet_id, "daf_id": daf_id}
    ).fetchall()

    return [
        MediaInfo(
            media_id=row.media_id,
            massechet_name=row.massechet_name,
            daf_name=row.daf_name,
        )
        for row in result
    ]
