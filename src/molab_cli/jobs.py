"""
Universal Job & Artifact Management Subsystem for MoLab Cloud Pods.
Persists job state, execution lifecycles, and output artifacts in local SQLite.
Accurately distinguishes controller state from remote process state.
"""

import json
import os
import shlex
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from molab_cli.exceptions import JobError, JobNotFoundError, SandboxOfflineError
from molab_cli.execution import RemoteExecutor
from molab_cli.sandbox import SandboxSession
from molab_cli.transfer import TransferManager


def get_default_db_path() -> str:
    """Return persistent SQLite database path in ~/.config/molab/jobs.db."""
    config_dir = os.path.expanduser("~/.config/molab")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "jobs.db")


class JobManager:
    """Manages persistent background jobs and output artifacts."""

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
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    workload_type TEXT,
                    notebook_id TEXT,
                    sandbox_id TEXT,
                    command TEXT,
                    remote_pid INTEGER,
                    remote_log_path TEXT,
                    remote_dir TEXT,
                    workdir TEXT,
                    status TEXT,
                    exit_code INTEGER,
                    created_at REAL,
                    started_at REAL,
                    completed_at REAL,
                    error_message TEXT,
                    metadata_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    remote_path TEXT,
                    local_path TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    created_at REAL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
            """)
            conn.commit()

    def submit_job(
        self,
        notebook_id: str,
        command: str,
        name: Optional[str] = None,
        workload_type: str = "custom",
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Submit and launch an asynchronous background job on the designated pod.
        """
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job_name = name or f"{workload_type}-{job_id[-6:]}"
        remote_dir = f"/workspace/jobs/{job_id}"
        remote_log = f"{remote_dir}/run.log"
        exit_file = f"{remote_dir}/exit_code"
        artifacts_dir = f"{remote_dir}/artifacts"

        session = SandboxSession(notebook_id)
        session.resolve()
        executor = RemoteExecutor(session)

        # Create job environment on remote pod
        setup_cmd = f"mkdir -p {remote_dir} {artifacts_dir}"
        executor.execute(setup_cmd, timeout=12.0)

        # Build runner wrapper that captures exit code reliably
        # The wrapper redirects command stdout/stderr to run.log and saves $? to exit_file
        wrapped_command = (
            f"( {command} ) > {remote_log} 2>&1 ; echo $? > {exit_file}"
        )

        remote_pid = executor.execute_background(
            cmd=wrapped_command,
            log_file=f"{remote_dir}/launcher.log",
            workdir=workdir or "/workspace",
            env=env,
        )

        now = time.time()
        job_meta = metadata or {}
        job_meta["env"] = env or {}
        job_meta["artifacts_dir"] = artifacts_dir

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO jobs (
                    id, name, workload_type, notebook_id, sandbox_id,
                    command, remote_pid, remote_log_path, remote_dir,
                    workdir, status, exit_code, created_at, started_at,
                    completed_at, error_message, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, job_name, workload_type, notebook_id, str(session.sandbox_id) if session.sandbox_id is not None else None,
                command, remote_pid, remote_log, remote_dir,
                workdir or "/workspace", "RUNNING", None, now, now,
                None, None, json.dumps(job_meta)
            ))
            conn.commit()

        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Retrieve job record from database."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise JobNotFoundError(job_id)
            d = dict(row)
            if d.get("metadata_json"):
                d["metadata"] = json.loads(d["metadata_json"])
            return d

    def list_jobs(self, limit: int = 50, notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List historic and active jobs."""
        with self._get_conn() as conn:
            query = "SELECT * FROM jobs"
            params: List[Any] = []
            if notebook_id:
                query += " WHERE notebook_id = ?"
                params.append(notebook_id)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            jobs = []
            for r in rows:
                d = dict(r)
                if d.get("metadata_json"):
                    d["metadata"] = json.loads(d["metadata_json"])
                jobs.append(d)
            return jobs

    def refresh_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Poll and synchronize job status with remote pod state.
        Handles process completion, pod expiration (LOST), and exit code harvesting.
        """
        job = self.get_job(job_id)
        if job["status"] in ("COMPLETED", "FAILED", "CANCELLED", "LOST"):
            return job

        notebook_id = job["notebook_id"]
        remote_pid = job["remote_pid"]
        exit_file = f"{job['remote_dir']}/exit_code"
        session = SandboxSession(notebook_id)

        try:
            session.resolve()
            executor = RemoteExecutor(session)
        except Exception as e:
            # Pod is stopped, expired, or unreachable
            now = time.time()
            self._update_job_status(
                job_id,
                status="LOST",
                error_message=f"Pod became offline or expired: {e}",
                completed_at=now,
            )
            return self.get_job(job_id)

        # Check if exit_code file exists
        has_exit_file = executor.execute(f"[ -f {exit_file} ] && echo 1 || echo 0", timeout=8.0).stdout.strip() == "1"

        if has_exit_file:
            exit_code_str = executor.execute(f"cat {exit_file}", timeout=8.0).stdout.strip()
            try:
                exit_code = int(exit_code_str)
            except ValueError:
                exit_code = 1

            new_status = "COMPLETED" if exit_code == 0 else "FAILED"
            now = time.time()
            self._update_job_status(
                job_id,
                status=new_status,
                exit_code=exit_code,
                completed_at=now,
            )
            # Register output artifacts automatically
            self._scan_and_register_artifacts(session, job_id, f"{job['remote_dir']}/artifacts")
            return self.get_job(job_id)

        # Exit file does not exist yet. Check if remote PID is still executing
        is_alive = executor.is_process_running(remote_pid)
        if not is_alive:
            # Process died without leaving exit_code (OOM killer or hard crash)
            now = time.time()
            self._update_job_status(
                job_id,
                status="FAILED",
                exit_code=137,
                error_message="Process terminated unexpectedly (possible OOM or SIGKILL).",
                completed_at=now,
            )
            return self.get_job(job_id)

        # Still running normally
        return job

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Terminate a running remote job."""
        job = self.get_job(job_id)
        if job["status"] not in ("RUNNING", "PENDING"):
            return job

        session = SandboxSession(job["notebook_id"])
        try:
            session.resolve()
            executor = RemoteExecutor(session)
            executor.kill_process(job["remote_pid"], signal=15)
        except Exception:
            pass

        now = time.time()
        self._update_job_status(
            job_id,
            status="CANCELLED",
            error_message="Job was cancelled by user.",
            completed_at=now,
        )
        return self.get_job(job_id)

    def get_job_logs(self, job_id: str, tail_lines: int = 100) -> str:
        """Fetch remote execution logs for the job."""
        job = self.get_job(job_id)
        session = SandboxSession(job["notebook_id"])
        try:
            session.resolve()
            executor = RemoteExecutor(session)
            return executor.read_logs(job["remote_log_path"], tail_lines=tail_lines)
        except Exception as e:
            return f"[Error fetching live logs from pod: {e}]"

    def _update_job_status(
        self,
        job_id: str,
        status: str,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE jobs
                SET status = ?, exit_code = ?, error_message = ?, completed_at = ?
                WHERE id = ?
            """, (status, exit_code, error_message, completed_at, job_id))
            conn.commit()

    def _scan_and_register_artifacts(self, session: SandboxSession, job_id: str, artifacts_dir: str) -> None:
        """Discover and record output artifacts created by the job."""
        try:
            transfer = TransferManager(session)
            manifest = transfer.build_remote_manifest(artifacts_dir)
            now = time.time()
            with self._get_conn() as conn:
                for rel_path, meta in manifest.items():
                    remote_full = f"{artifacts_dir}/{rel_path}"
                    conn.execute("""
                        INSERT INTO artifacts (job_id, remote_path, local_path, size_bytes, sha256, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (job_id, remote_full, None, meta.get("size", 0), meta.get("sha256"), now))
                conn.commit()
        except Exception:
            pass

    def list_artifacts(self, job_id: str) -> List[Dict[str, Any]]:
        """List registered artifacts for a given job."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM artifacts WHERE job_id = ?", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def download_artifacts(self, job_id: str, destination_dir: str) -> List[Dict[str, Any]]:
        """Download all registered artifacts for a completed job."""
        job = self.get_job(job_id)
        artifacts = self.list_artifacts(job_id)
        if not artifacts:
            return []

        session = SandboxSession(job["notebook_id"])
        transfer = TransferManager(session)
        os.makedirs(destination_dir, exist_ok=True)

        results = []
        for art in artifacts:
            remote_path = art["remote_path"]
            filename = os.path.basename(remote_path)
            local_target = os.path.join(destination_dir, filename)
            summary = transfer.download_file(remote_path, local_target, verify_checksum=True)
            results.append(summary)

        return results
