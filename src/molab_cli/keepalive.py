"""
Autonomous Anti-Idle Heartbeat & Session Renewal Engine.
Keeps MoLab Blackwell GPU pods alive indefinitely, preventing the 30-minute idle timeout.
Detects pod restarts and triggers automatic workspace restoration.
"""

import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from molab_cli.exceptions import MoLabError
from molab_cli.sandbox import SandboxSession
from molab_cli.snapshots import SnapshotManager


def get_default_db_path() -> str:
    """Return persistent SQLite database path."""
    config_dir = os.path.expanduser("~/.config/molab")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "jobs.db")


def get_keepalive_log_path(notebook_id: str) -> str:
    """Return persistent log file path for a notebook keepalive daemon."""
    config_dir = os.path.expanduser("~/.config/molab")
    os.makedirs(config_dir, exist_ok=True)
    clean_id = notebook_id.replace("nb_", "")
    return os.path.join(config_dir, f"keepalive_{clean_id}.log")


class KeepaliveError(MoLabError):
    """Raised when keepalive operation fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="KEEPALIVE_ERROR", details=details)


class KeepaliveManager:
    """Manages anti-idle heartbeat daemons, token renewals, and auto-resurrection."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_db_path()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS keepalives (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL UNIQUE,
                    sandbox_id TEXT,
                    pid INTEGER,
                    status TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    max_hours REAL,
                    auto_restore INTEGER NOT NULL DEFAULT 1,
                    started_at REAL NOT NULL,
                    last_heartbeat_at REAL,
                    heartbeat_count INTEGER NOT NULL DEFAULT 0,
                    restores_triggered INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    log_path TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_keepalives_nb ON keepalives(notebook_id);")

    def start_daemon(
        self,
        notebook_id: str,
        interval: int = 120,
        max_hours: Optional[float] = None,
        auto_restore: bool = True,
    ) -> Dict[str, Any]:
        """
        Launch the keepalive heartbeat engine as an autonomous detached background daemon.
        """
        nb_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        existing = self.get_status(nb_id)
        if existing and existing["status"] == "running":
            raise KeepaliveError(
                f"Keepalive daemon already running for {nb_id} (PID {existing['pid']})"
            )

        # Acquire Termux wake lock on Android so phone CPU doesn't sleep in background
        for wl_cmd in ("/data/data/com.termux/files/usr/bin/termux-wake-lock", "termux-wake-lock"):
            if os.path.exists(wl_cmd):
                try:
                    subprocess.run([wl_cmd], capture_output=True)
                    break
                except Exception:
                    pass

        log_path = get_keepalive_log_path(nb_id)
        cmd = [
            sys.executable,
            "-m",
            "molab_cli.cli",
            "keepalive",
            "run",
            nb_id,
            "--interval",
            str(interval),
        ]
        if max_hours:
            cmd.extend(["--max-hours", str(max_hours)])
        if auto_restore:
            cmd.append("--auto-restore")
        else:
            cmd.append("--no-auto-restore")

        # Open log file for output redirection
        log_f = open(log_path, "a", encoding="utf-8")

        # Spawn detached background process surviving terminal disconnects
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=log_f,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        now = time.time()
        record_id = f"ka_{nb_id}"
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO keepalives (
                    id, notebook_id, sandbox_id, pid, status, interval_seconds,
                    max_hours, auto_restore, started_at, last_heartbeat_at,
                    heartbeat_count, restores_triggered, error_message, log_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                nb_id,
                None,
                proc.pid,
                "running",
                interval,
                max_hours,
                1 if auto_restore else 0,
                now,
                now,
                0,
                0,
                None,
                log_path,
            ))

        return {
            "id": record_id,
            "notebook_id": nb_id,
            "pid": proc.pid,
            "status": "running",
            "interval_seconds": interval,
            "max_hours": max_hours,
            "auto_restore": auto_restore,
            "log_path": log_path,
            "started_at": now,
        }

    def stop_daemon(self, notebook_id: str) -> bool:
        """Terminate the running keepalive daemon."""
        nb_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        status = self.get_status(nb_id)
        if not status:
            return False

        pid = status.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                # Verify termination; send SIGKILL if still active
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            except OSError:
                pass

        with self._get_conn() as conn:
            conn.execute(
                "UPDATE keepalives SET status = 'stopped' WHERE notebook_id = ?",
                (nb_id,),
            )
        return True

    def get_status(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Fetch current keepalive status, verifying live process existence."""
        nb_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM keepalives WHERE notebook_id = ?", (nb_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)

        pid = res.get("pid")
        if res.get("status") == "running" and pid:
            # Check if process is actually running
            is_alive = False
            try:
                os.kill(pid, 0)
                is_alive = True
            except OSError:
                is_alive = False

            if not is_alive:
                res["status"] = "stopped"
                with self._get_conn() as conn:
                    conn.execute(
                        "UPDATE keepalives SET status = 'stopped' WHERE notebook_id = ?",
                        (nb_id,),
                    )

        return res

    def list_keepalives(self) -> List[Dict[str, Any]]:
        """List all tracked keepalive daemons."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM keepalives ORDER BY started_at DESC")
            rows = [dict(r) for r in cur.fetchall()]

        # Refresh live statuses
        for item in rows:
            if item.get("status") == "running" and item.get("pid"):
                try:
                    os.kill(item["pid"], 0)
                except OSError:
                    item["status"] = "stopped"
        return rows

    def ping_data_plane(self, session: SandboxSession, timeout: float = 8.0) -> bool:
        """
        Ping data plane proxy endpoint to refresh the CoreWeave idle connection timer.
        """
        url = f"{session.base_url}/api/usage?token={session.auth_token}"
        req = urllib.request.Request(url, headers={"User-Agent": "MoLab-Keepalive/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            # Fallback to status endpoint
            try:
                url_st = f"{session.base_url}/api/status?token={session.auth_token}"
                req_st = urllib.request.Request(url_st, headers={"User-Agent": "MoLab-Keepalive/2.0"})
                with urllib.request.urlopen(req_st, timeout=timeout) as resp_st:
                    return resp_st.status == 200
            except Exception:
                return False

    def inject_in_pod_guard(self, session: SandboxSession) -> bool:
        """
        Deploy and start self-sustaining in-pod guard daemon directly inside the container.
        Keeps Marimo process active internally even if external network disconnects.
        """
        try:
            check = session.execute_command("pgrep -f _molab_guard.py || echo 0", timeout=8.0).strip()
            if check != "0" and check:
                return True
            guard_py = (
                "import time, urllib.request\\n"
                "while True:\\n"
                "    try:\\n"
                "        with urllib.request.urlopen('http://localhost:8080/api/status', timeout=5):\\n"
                "            pass\\n"
                "        with open('/tmp/_guard.ts', 'w') as f:\\n"
                "            f.write(str(time.time()))\\n"
                "    except Exception:\\n"
                "        pass\\n"
                "    time.sleep(25)\\n"
            )
            import shlex
            deploy_cmd = (
                f"echo {shlex.quote(guard_py)} > /tmp/_molab_guard.py && "
                "nohup python3 /tmp/_molab_guard.py > /dev/null 2>&1 &"
            )
            session.execute_command(deploy_cmd, timeout=12.0)
            return True
        except Exception:
            return False

    def run_loop(
        self,
        notebook_id: str,
        interval: int = 120,
        max_hours: Optional[float] = None,
        auto_restore: bool = True,
    ) -> None:
        """
        Execute the keepalive heartbeat and renewal loop.
        Designed to run inside the background worker process.
        """
        nb_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] [Keepalive] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        logger = logging.getLogger("molab_keepalive")

        stop_requested = False

        def handle_signal(sig, frame):
            nonlocal stop_requested
            logger.info("Termination signal received. Exiting keepalive loop.")
            stop_requested = True

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        session = SandboxSession(nb_id)
        snap_mgr = SnapshotManager(self.db_path)

        existing_status = self.get_status(nb_id)
        last_known_sandbox_id: Optional[str] = existing_status.get("sandbox_id") if existing_status else None

        start_time = time.time()
        heartbeat_count = 0
        restores_triggered = 0

        logger.info(
            f"Starting keepalive loop for {nb_id} (interval={interval}s, max_hours={max_hours}, auto_restore={auto_restore})"
        )

        while not stop_requested:
            # Check maximum runtime cutoff
            if max_hours is not None:
                elapsed_hours = (time.time() - start_time) / 3600.0
                if elapsed_hours >= max_hours:
                    logger.info(f"Reached max allowed runtime of {max_hours} hours. Shutting down keepalive.")
                    break

            try:
                # 1. Resolve session and renew token if expiring soon (< 15 min remaining)
                now = time.time()
                need_refresh = False
                if session.expires_at is None or (session.expires_at - now) < 900:
                    need_refresh = True

                session.resolve(force_refresh=need_refresh)

                # 2. Check for Pod Reset / Resurrection
                if last_known_sandbox_id is not None and session.sandbox_id != last_known_sandbox_id:
                    logger.warning(
                        f"Pod reset detected! Sandbox changed from {last_known_sandbox_id} to {session.sandbox_id}."
                    )
                    if auto_restore:
                        # First check in-notebook vault (100% on MoLab)
                        try:
                            from molab_cli.vault import MoLabVault
                            v_info = MoLabVault(nb_id).inspect_vault()
                            if v_info.get("has_vault"):
                                logger.info("Restoring workspace from in-notebook vault (100% on MoLab)...")
                                try:
                                    MoLabVault(nb_id).unpack_workspace()
                                    logger.info("Successfully unpacked in-notebook vault into /workspace.")
                                except Exception as ve:
                                    logger.error(f"Vault auto-unpack error: {ve}")
                        except Exception as vault_err:
                            logger.debug(f"Vault auto-restore check info: {vault_err}")

                        # Also check snapshot manager
                        logger.info("Checking for workspace snapshot restoration...")
                        try:
                            res = snap_mgr.restore_snapshot(nb_id)
                            restores_triggered += 1
                            logger.info(
                                f"Successfully auto-restored {res['files_restored']} files from snapshot {res['snapshot_id']} in {res['duration_seconds']}s"
                            )
                        except Exception as restore_err:
                            logger.debug(f"Snapshot restore info: {restore_err}")

                last_known_sandbox_id = session.sandbox_id

                # 3. Ensure In-Pod Self-Sustaining Guard is running inside container
                self.inject_in_pod_guard(session)

                # 4. Ping Data Plane HTTP Proxy to keep CoreWeave TCP socket alive
                ping_ok = self.ping_data_plane(session)
                if not ping_ok:
                    # Fallback to WebSocket lightweight probe
                    logger.info("HTTP ping timed out, probing via WebSocket command...")
                    ws_out = session.execute_command("echo ping > /dev/null 2>&1", timeout=10.0)

                heartbeat_count += 1
                remaining_ttl = int(session.expires_at - time.time()) if session.expires_at else -1
                logger.info(
                    f"Heartbeat #{heartbeat_count} OK. Pod: {session.sandbox_id} | Session TTL: {remaining_ttl}s remaining"
                )

                # 4. Update status in database
                with self._get_conn() as conn:
                    conn.execute("""
                        UPDATE keepalives SET
                            sandbox_id = ?,
                            last_heartbeat_at = ?,
                            heartbeat_count = ?,
                            restores_triggered = ?,
                            status = 'running',
                            error_message = NULL
                        WHERE notebook_id = ?
                    """, (session.sandbox_id, time.time(), heartbeat_count, restores_triggered, nb_id))

            except Exception as err:
                logger.error(f"Heartbeat cycle error: {err}")
                with self._get_conn() as conn:
                    conn.execute("""
                        UPDATE keepalives SET
                            error_message = ?
                        WHERE notebook_id = ?
                    """, (str(err), nb_id))

            # Sleep in 1-second slices so we respond immediately to SIGTERM
            sleep_deadline = time.time() + interval
            while time.time() < sleep_deadline and not stop_requested:
                time.sleep(1.0)

        # Mark finished
        logger.info(f"Keepalive loop stopped for {nb_id}. Total heartbeats: {heartbeat_count}.")
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE keepalives SET status = 'stopped' WHERE notebook_id = ?",
                (nb_id,),
            )
