"""
Production AI Gateway Database & Virtual Key Governance Engine.
Backs persistent multi-tenant API keys, token metering, request audit logging,
and usage analytics with high-concurrency SQLite WAL.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

DB_DIR = os.path.expanduser("~/.config/molab")
DB_PATH = os.path.join(DB_DIR, "gateway.db")


def hash_key(raw_key: str) -> str:
    """Generate SHA-256 hash of API key for secure storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class GatewayDB:
    """SQLite WAL storage manager for AI Gateway keys, metering, and logs."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init_db(self) -> None:
        """Create tables and default key if database is fresh."""
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    hashed_key TEXT UNIQUE NOT NULL,
                    rpm_limit INTEGER DEFAULT 60,
                    tpm_limit INTEGER DEFAULT 60000,
                    is_active INTEGER DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    total_requests INTEGER DEFAULT 0,
                    total_prompt_tokens INTEGER DEFAULT 0,
                    total_completion_tokens INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    req_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    ttft_ms REAL DEFAULT 0.0,
                    total_duration_ms REAL DEFAULT 0.0,
                    status_code INTEGER DEFAULT 200,
                    timestamp INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_req_logs_key_ts ON request_logs(key_id, timestamp);
            """)

            # Ensure default master key exists
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM api_keys")
            if cur.fetchone()[0] == 0:
                default_raw_key = "sk-molab-blackwell-cluster"
                k_id = "key_default_master"
                cur.execute("""
                    INSERT INTO api_keys (
                        key_id, name, key_prefix, hashed_key, rpm_limit, tpm_limit, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    k_id,
                    "Default Master Key",
                    default_raw_key[:12] + "...",
                    hash_key(default_raw_key),
                    120,
                    150000,
                    1,
                    int(time.time()),
                ))
            conn.commit()

    def create_key(
        self,
        name: str,
        rpm_limit: int = 60,
        tpm_limit: int = 60000,
    ) -> Tuple[str, Dict[str, Any]]:
        """Generate and store a new virtual API key."""
        random_hex = secrets.token_hex(16)
        raw_key = f"sk-molab-{random_hex}"
        key_id = f"key_{secrets.token_hex(6)}"
        created_at = int(time.time())
        prefix = raw_key[:14] + "..."
        hashed = hash_key(raw_key)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO api_keys (
                    key_id, name, key_prefix, hashed_key, rpm_limit, tpm_limit, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (key_id, name, prefix, hashed, rpm_limit, tpm_limit, created_at))
            conn.commit()

        key_info = {
            "key_id": key_id,
            "name": name,
            "key_prefix": prefix,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "is_active": True,
            "created_at": created_at,
        }
        return raw_key, key_info

    def validate_key(self, raw_key: str) -> Optional[Dict[str, Any]]:
        """Validate an incoming bearer key against the database."""
        if not raw_key:
            return None
        hashed = hash_key(raw_key.strip())
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT key_id, name, key_prefix, rpm_limit, tpm_limit, is_active,
                       total_requests, total_prompt_tokens, total_completion_tokens
                FROM api_keys
                WHERE hashed_key = ? AND is_active = 1
            """, (hashed,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def record_usage(
        self,
        key_id: str,
        req_id: str,
        endpoint: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ttft_ms: float = 0.0,
        total_duration_ms: float = 0.0,
        status_code: int = 200,
    ) -> None:
        """Atomically record request usage and increment key totals."""
        now = int(time.time())
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO request_logs (
                    req_id, key_id, endpoint, model, prompt_tokens, completion_tokens,
                    ttft_ms, total_duration_ms, status_code, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req_id, key_id, endpoint, model, prompt_tokens, completion_tokens,
                ttft_ms, total_duration_ms, status_code, now
            ))

            conn.execute("""
                UPDATE api_keys
                SET total_requests = total_requests + 1,
                    total_prompt_tokens = total_prompt_tokens + ?,
                    total_completion_tokens = total_completion_tokens + ?
                WHERE key_id = ?
            """, (prompt_tokens, completion_tokens, key_id))
            conn.commit()

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all registered API keys and usage statistics."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT key_id, name, key_prefix, rpm_limit, tpm_limit, is_active,
                       created_at, total_requests, total_prompt_tokens, total_completion_tokens
                FROM api_keys
                ORDER BY created_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]

    def revoke_key(self, key_id: str) -> bool:
        """Deactivate an API key immediately."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE api_keys SET is_active = 0 WHERE key_id = ?", (key_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_analytics(self) -> Dict[str, Any]:
        """Aggregate high-level system usage and latency metrics."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as total_requests,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                    COALESCE(AVG(ttft_ms), 0.0) as avg_ttft_ms,
                    COALESCE(AVG(total_duration_ms), 0.0) as avg_latency_ms
                FROM request_logs
            """)
            summary = dict(cur.fetchone())

            cur.execute("SELECT COUNT(*) FROM api_keys WHERE is_active = 1")
            summary["active_keys_count"] = cur.fetchone()[0]

            # Last 5 requests
            cur.execute("""
                SELECT req_id, key_id, model, prompt_tokens, completion_tokens,
                       ttft_ms, total_duration_ms, status_code, timestamp
                FROM request_logs
                ORDER BY id DESC LIMIT 5
            """)
            summary["recent_requests"] = [dict(r) for r in cur.fetchall()]

            return summary


# Default singleton instance
default_gateway_db = GatewayDB()
