"""
Unified Database Management Engine for MoLab CLI.
Enforces SQLite Write-Ahead Logging (WAL), transaction durability,
concurrency safety, and deterministic connection cleanup across all subsystems.
"""

from contextlib import contextmanager
import os
import sqlite3
from typing import Generator, Optional


def configure_connection(conn: sqlite3.Connection, wal_mode: bool = True) -> sqlite3.Connection:
    """Apply high-concurrency PRAGMAs to an open SQLite connection."""
    conn.row_factory = sqlite3.Row
    if wal_mode:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=15000;")
        except sqlite3.OperationalError:
            pass
    return conn


@contextmanager
def db_session(db_path: str, timeout: float = 15.0, wal_mode: bool = True) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields an active SQLite connection and guarantees
    commit/rollback AND closure upon exit, preventing Windows file locking
    and SQLite concurrency deadlocks.
    """
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=timeout)
    configure_connection(conn, wal_mode=wal_mode)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class BaseDatabaseManager:
    """Base class for thread-safe SQLite-backed subsystems."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(os.path.expanduser(db_path))
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    @contextmanager
    def session(self, timeout: float = 15.0) -> Generator[sqlite3.Connection, None, None]:
        """Open a scoped database session that automatically commits and closes."""
        with db_session(self.db_path, timeout=timeout) as conn:
            yield conn
