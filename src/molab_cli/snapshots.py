"""
Workspace Snapshot, Checkpoint & Auto-Persistence Engine.
Preserves ephemeral pod /workspace contents across resets, restarts, and timeouts.
Provides streaming snapshot creation, local SQLite manifest tracking, and 1-click restore.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from molab_cli.exceptions import MoLabError
from molab_cli.sandbox import SandboxSession


def get_default_db_path() -> str:
    """Return persistent SQLite database path."""
    config_dir = os.path.expanduser("~/.config/molab")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "jobs.db")


def get_snapshots_dir(notebook_id: Optional[str] = None) -> str:
    """Return local directory for persistent workspace archives."""
    base = os.path.expanduser("~/.config/molab/snapshots")
    if notebook_id:
        p = os.path.join(base, notebook_id)
    else:
        p = base
    os.makedirs(p, exist_ok=True)
    return p


class SnapshotError(MoLabError):
    """Raised when snapshot creation or restoration fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="SNAPSHOT_ERROR", details=details)


class SnapshotManager:
    """Manages workspace snapshots, checkpoints, and automated restoration."""

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
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    name TEXT,
                    size_bytes INTEGER NOT NULL,
                    file_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    local_archive_path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_nb ON snapshots(notebook_id);")

    def create_snapshot(
        self,
        notebook_id: str,
        name: Optional[str] = None,
        remote_path: str = "/workspace",
        excludes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a compressed snapshot of the pod's workspace and stream it to local storage.
        """
        session = SandboxSession(notebook_id)
        session.resolve()

        snap_id = f"snap_{uuid.uuid4().hex[:10]}"
        snap_name = name or f"Snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"
        remote_tar = f"/tmp/_{snap_id}.tar.gz"

        # Exclude common cache directories to keep snapshots lean and fast
        default_excludes = ["__pycache__", "*.pyc", ".cache", ".git/objects"]
        if excludes:
            default_excludes.extend(excludes)

        exclude_flags = " ".join([f"--exclude='{exc}'" for exc in default_excludes])

        # 1. Package remote directory on the pod
        pack_cmd = (
            f"mkdir -p {remote_path} && "
            f"cd {remote_path} && "
            f"tar -czf {remote_tar} {exclude_flags} . 2>/dev/null && "
            f"stat -c %s {remote_tar} && "
            f"sha256sum {remote_tar} | awk '{{print $1}}' && "
            f"find . -type f | wc -l"
        )
        pack_out = session.execute_command(pack_cmd, timeout=120.0).strip().split("\n")
        if len(pack_out) < 3:
            raise SnapshotError(f"Failed to create remote snapshot archive: {' '.join(pack_out)}")

        try:
            remote_size = int(pack_out[-3].strip())
            remote_sha256 = pack_out[-2].strip()
            file_count = int(pack_out[-1].strip())
        except ValueError as err:
            raise SnapshotError(f"Invalid archive metadata from pod: {pack_out}") from err

        # 2. Stream tarball from pod directly to local snapshot directory
        local_dir = get_snapshots_dir(session.notebook_id)
        local_archive = os.path.join(local_dir, f"{snap_id}.tar.gz")

        download_url = f"{session.base_url}/api/files/download?path={remote_tar}&token={session.auth_token}"
        curl_cmd = ["curl", "-s", "-f", "-L", "-o", local_archive, download_url]
        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            session.execute_command(f"rm -f {remote_tar}")
            raise SnapshotError(f"Failed to download snapshot archive from pod: {res.stderr}")

        # 3. Clean up temporary archive on pod
        session.execute_command(f"rm -f {remote_tar}")

        # 4. Verify local archive checksum
        hasher = hashlib.sha256()
        with open(local_archive, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        local_sha256 = hasher.hexdigest()

        if local_sha256 != remote_sha256:
            if os.path.exists(local_archive):
                os.remove(local_archive)
            raise SnapshotError(
                f"Snapshot checksum mismatch: remote {remote_sha256} != local {local_sha256}"
            )

        now = time.time()
        record = {
            "id": snap_id,
            "notebook_id": session.notebook_id,
            "name": snap_name,
            "size_bytes": remote_size,
            "file_count": file_count,
            "sha256": local_sha256,
            "remote_path": remote_path,
            "local_archive_path": local_archive,
            "created_at": now,
            "metadata": json.dumps({
                "excludes": default_excludes,
                "sandbox_id": session.sandbox_id,
            }),
        }

        # 5. Persist record in SQLite
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO snapshots (
                    id, notebook_id, name, size_bytes, file_count, sha256,
                    remote_path, local_archive_path, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["id"], record["notebook_id"], record["name"], record["size_bytes"],
                record["file_count"], record["sha256"], record["remote_path"],
                record["local_archive_path"], record["created_at"], record["metadata"]
            ))

        return record

    def restore_snapshot(
        self,
        notebook_id: str,
        snapshot_id: Optional[str] = None,
        remote_path: str = "/workspace",
    ) -> Dict[str, Any]:
        """
        Restore a snapshot into the remote pod's /workspace directory.
        If snapshot_id is not provided, restores the most recent snapshot for the notebook.
        """
        session = SandboxSession(notebook_id)
        session.resolve()

        if snapshot_id:
            snap = self.get_snapshot(snapshot_id)
            if not snap:
                raise SnapshotError(f"Snapshot not found: {snapshot_id}")
        else:
            snap = self.get_latest_snapshot(session.notebook_id)
            if not snap:
                raise SnapshotError(f"No existing snapshots found for notebook {session.notebook_id}")

        local_archive = snap["local_archive_path"]
        if not os.path.exists(local_archive):
            raise SnapshotError(f"Local snapshot archive file missing: {local_archive}")

        t0 = time.time()
        remote_tar_name = f"_restore_{snap['id']}.tar.gz"

        # 1. Upload local archive directly to pod /tmp via native Marimo HTTP multipart streaming
        upload_url = f"{session.base_url}/api/files/create?token={session.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-X", "POST", upload_url,
            "-F", "path=/tmp",
            "-F", "type=file",
            "-F", f"name={remote_tar_name}",
            "-F", f"file=@{local_archive}",
        ]
        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise SnapshotError(f"Failed to stream snapshot archive to pod: {res.stderr}")

        # 2. Extract archive on the pod
        unpack_cmd = (
            f"mkdir -p {remote_path} && "
            f"tar -xzf /tmp/{remote_tar_name} -C {remote_path} && "
            f"rm -f /tmp/{remote_tar_name} && "
            f"find {remote_path} -type f | wc -l"
        )
        count_out = session.execute_command(unpack_cmd, timeout=120.0).strip()
        try:
            restored_count = int(count_out.split("\n")[-1].strip())
        except ValueError:
            restored_count = snap["file_count"]

        duration = round(time.time() - t0, 2)
        return {
            "snapshot_id": snap["id"],
            "notebook_id": session.notebook_id,
            "name": snap["name"],
            "remote_path": remote_path,
            "size_bytes": snap["size_bytes"],
            "files_restored": restored_count,
            "duration_seconds": duration,
        }

    def list_snapshots(
        self,
        notebook_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List snapshots ordered by creation date descending."""
        with self._get_conn() as conn:
            if notebook_id:
                nb = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
                cur = conn.execute(
                    "SELECT * FROM snapshots WHERE notebook_id = ? ORDER BY created_at DESC LIMIT ?",
                    (nb, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM snapshots ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Fetch snapshot record by ID."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_latest_snapshot(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent snapshot for a specific notebook."""
        nb = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT * FROM snapshots WHERE notebook_id = ? ORDER BY created_at DESC LIMIT 1",
                (nb,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete local snapshot archive and database record."""
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            return False

        local_path = snap["local_archive_path"]
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass

        with self._get_conn() as conn:
            conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        return True
