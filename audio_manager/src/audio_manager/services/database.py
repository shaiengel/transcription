import os
from contextlib import contextmanager
from datetime import date
from typing import Generator
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine

from audio_manager.models.schemas import CalendarEntry, MediaEntry

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


def get_massechet_sefaria_name(conn: Connection, massechet_id: int) -> str | None:
    """Get the Sefaria folder name for a massechet from massechet_stein table.

    The massechet_stein table uses the same IDs as the Calendar table (283-322)
    and contains names that match Sefaria folder names when lowercased.
    """
    query = text("""
        SELECT name
        FROM [vps_daf-yomi].[dbo].[massechet_stein]
        WHERE massechetId = :massechet_id
    """)
    result = conn.execute(query, {"massechet_id": massechet_id}).fetchone()
    return result[0].lower() if result else None


def get_media_links(conn: Connection, massechet_id: int, daf_id: int) -> list[MediaEntry]:
    """Get media links for a specific massechet and daf."""
    # query = text("""
    #     SELECT media_id, media_link, maggid_description, massechet_name,
    #            daf_name, language_en, media_duration, file_type
    #     FROM [vps_daf-yomi].[dbo].[View_Media]
    #     WHERE massechet_id = :massechet_id AND daf_id = :daf_id
    #       AND media_duration IS NOT NULL AND media_duration > 0
    # """)
    query = text("""
        SELECT media_id, media_link, maggid_description, massechet_name,
               daf_name, language_en, media_duration, file_type
        FROM [vps_daf-yomi].[dbo].[View_Media]
        WHERE massechet_id = :massechet_id AND daf_id = :daf_id
          AND media_duration IS NOT NULL AND media_duration > 0
          AND language_en = 'hebrew'
    """)
    result = conn.execute(
        query, {"massechet_id": massechet_id, "daf_id": daf_id}
    ).fetchall()

    return [
        MediaEntry(
            media_id=row.media_id,
            media_link=row.media_link,
            maggid_description=row.maggid_description,
            massechet_name=row.massechet_name,
            daf_name=row.daf_name,
            details="",
            language=row.language_en,
            media_duration=row.media_duration,
            file_type=row.file_type,
        )
        for row in result
    ]
