"""
Universal Streaming File Transfer, Integrity Verification, and Directory Sync Engine.
Uses native Marimo HTTP REST endpoints (/api/files/create and /api/files/download)
with SHA-256 checksums, chunked streaming, and manifest comparison.
"""

import base64
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from molab_cli.exceptions import FileTransferError
from molab_cli.sandbox import SandboxSession


def calculate_local_sha256(filepath: str, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a local file in memory-efficient chunks."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


class TransferManager:
    """Manages high-speed file transfers and directory synchronization."""

    def __init__(self, session: SandboxSession):
        self.session = session

    def upload_file(
        self,
        local_path: str,
        remote_path: Optional[str] = None,
        verify_checksum: bool = True,
    ) -> Dict[str, Any]:
        """
        Upload a single file directly via Marimo POST /api/files/create multipart HTTP API.
        Optionally verifies SHA-256 checksum integrity against remote file.
        """
        abs_local = os.path.abspath(os.path.expanduser(local_path))
        if not os.path.isfile(abs_local):
            raise FileTransferError(f"Local file does not exist: {local_path}")

        self.session.resolve()
        filename = os.path.basename(abs_local)
        file_size = os.path.getsize(abs_local)

        if remote_path and (remote_path.endswith("/") or self._is_remote_dir(remote_path)):
            dest_dir = remote_path.rstrip("/")
            dest_file = f"{dest_dir}/{filename}"
        else:
            dest_file = remote_path or f"/workspace/{filename}"
            dest_dir = os.path.dirname(dest_file) or "/workspace"

        # Ensure destination directory exists on pod
        self.session.execute_command(f"mkdir -p {shlex.quote(dest_dir)}")

        start_time = time.time()
        local_hash = calculate_local_sha256(abs_local) if verify_checksum else None

        # Execute upload using curl streaming
        upload_url = f"{self.session.base_url}/api/files/create?token={self.session.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-X", "POST",
            upload_url,
            "-F", f"path={dest_dir}",
            "-F", "type=file",
            "-F", f"name={os.path.basename(dest_file)}",
            "-F", f"file=@{abs_local}",
        ]

        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise FileTransferError(
                f"Failed to upload {local_path} to pod: {res.stderr}",
                details={"exit_code": res.returncode, "destination": dest_file},
            )

        duration = max(0.001, time.time() - start_time)
        speed_mb = (file_size / (1024 * 1024)) / duration

        checksum_verified = True
        remote_hash = None
        if verify_checksum:
            remote_hash = self.get_remote_file_sha256(dest_file)
            if remote_hash != local_hash:
                checksum_verified = False
                raise FileTransferError(
                    f"Checksum mismatch for {local_path}: local={local_hash} vs remote={remote_hash}",
                    hint="The upload payload may have been truncated or corrupted during transit.",
                    details={"local_sha256": local_hash, "remote_sha256": remote_hash},
                )

        return {
            "direction": "upload",
            "local_path": abs_local,
            "remote_path": dest_file,
            "size_bytes": file_size,
            "duration_seconds": round(duration, 3),
            "speed_mb_s": round(speed_mb, 2),
            "sha256": local_hash,
            "verified": checksum_verified,
        }

    def download_file(
        self,
        remote_path: str,
        local_path: Optional[str] = None,
        verify_checksum: bool = True,
    ) -> Dict[str, Any]:
        """
        Download a file from the pod directly to disk via GET /api/files/download.
        Streams chunked bytes to a temporary file before atomic rename.
        """
        self.session.resolve()
        dest = local_path or os.path.basename(remote_path.rstrip("/"))
        abs_dest = os.path.abspath(os.path.expanduser(dest))

        if os.path.isdir(abs_dest):
            abs_dest = os.path.join(abs_dest, os.path.basename(remote_path))

        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)

        remote_hash = None
        if verify_checksum:
            remote_hash = self.get_remote_file_sha256(remote_path)

        start_time = time.time()
        temp_dest = f"{abs_dest}.tmp_{uuid.uuid4().hex[:6]}"

        download_url = f"{self.session.base_url}/api/files/download?path={remote_path}&token={self.session.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-L",
            "-o", temp_dest,
            download_url,
        ]
        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            if os.path.exists(temp_dest):
                os.remove(temp_dest)
            raise FileTransferError(
                f"Failed to download {remote_path} from pod: {res.stderr}",
                details={"remote_path": remote_path, "exit_code": res.returncode},
            )

        # Atomic rename
        os.replace(temp_dest, abs_dest)
        file_size = os.path.getsize(abs_dest)
        duration = max(0.001, time.time() - start_time)
        speed_mb = (file_size / (1024 * 1024)) / duration

        checksum_verified = True
        local_hash = None
        if verify_checksum:
            local_hash = calculate_local_sha256(abs_dest)
            if local_hash != remote_hash:
                checksum_verified = False
                raise FileTransferError(
                    f"Checksum mismatch for downloaded file {remote_path}: expected {remote_hash}, got {local_hash}",
                    details={"expected_sha256": remote_hash, "downloaded_sha256": local_hash},
                )

        return {
            "direction": "download",
            "remote_path": remote_path,
            "local_path": abs_dest,
            "size_bytes": file_size,
            "duration_seconds": round(duration, 3),
            "speed_mb_s": round(speed_mb, 2),
            "sha256": local_hash,
            "verified": checksum_verified,
        }

    def upload_dir(
        self,
        local_dir: str,
        remote_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pack local directory into temporary tarball and stream upload."""
        abs_local = os.path.abspath(os.path.expanduser(local_dir))
        if not os.path.isdir(abs_local):
            raise FileTransferError(f"Local directory does not exist: {local_dir}")

        dirname = os.path.basename(abs_local.rstrip("/"))
        target_remote = remote_dir or f"/workspace/{dirname}"
        self.session.execute_command(f"mkdir -p {shlex.quote(target_remote)}")

        temp_dir = tempfile.gettempdir()
        tar_name = f"_up_{uuid.uuid4().hex[:8]}.tar.gz"
        temp_tar = os.path.join(temp_dir, tar_name)

        start_time = time.time()
        try:
            subprocess.run(
                ["tar", "-czf", temp_tar, "-C", os.path.dirname(abs_local), dirname],
                check=True, capture_output=True
            )
            tar_size = os.path.getsize(temp_tar)

            # Upload tarball
            upload_url = f"{self.session.base_url}/api/files/create?token={self.session.auth_token}"
            curl_cmd = [
                "curl", "-s", "-f", "-X", "POST",
                upload_url,
                "-F", "path=/tmp",
                "-F", "type=file",
                "-F", f"name={tar_name}",
                "-F", f"file=@{temp_tar}",
            ]
            res = subprocess.run(curl_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise FileTransferError(f"Directory upload failed: {res.stderr}")

            # Extract on pod and cleanup remote tarball
            self.session.execute_command(
                f"tar -xzf /tmp/{tar_name} -C $(dirname {shlex.quote(target_remote)}) && rm -f /tmp/{tar_name}"
            )
            duration = max(0.001, time.time() - start_time)
            return {
                "direction": "upload_dir",
                "local_dir": abs_local,
                "remote_dir": target_remote,
                "size_bytes": tar_size,
                "duration_seconds": round(duration, 3),
            }
        finally:
            if os.path.exists(temp_tar):
                os.remove(temp_tar)

    def download_dir(
        self,
        remote_dir: str,
        local_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Archive remote directory into /tmp on pod and download."""
        self.session.resolve()
        dest = local_dir or os.path.basename(remote_dir.rstrip("/"))
        abs_dest = os.path.abspath(os.path.expanduser(dest))

        tar_name = f"_dl_{uuid.uuid4().hex[:8]}.tar.gz"
        parent_dir = self.session.execute_command(f"dirname {shlex.quote(remote_dir)}").strip()
        base_name = self.session.execute_command(f"basename {shlex.quote(remote_dir)}").strip()

        start_time = time.time()
        self.session.execute_command(f"tar -czf /tmp/{tar_name} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}")

        temp_dir = tempfile.gettempdir()
        temp_tar = os.path.join(temp_dir, tar_name)
        try:
            download_url = f"{self.session.base_url}/api/files/download?path=/tmp/{tar_name}&token={self.session.auth_token}"
            subprocess.run(["curl", "-s", "-f", "-L", "-o", temp_tar, download_url], check=True)

            os.makedirs(abs_dest, exist_ok=True)
            extract_target = os.path.dirname(abs_dest) if not os.path.isdir(abs_dest) else abs_dest
            subprocess.run(["tar", "-xzf", temp_tar, "-C", extract_target], check=True)

            self.session.execute_command(f"rm -f /tmp/{tar_name}")
            tar_size = os.path.getsize(temp_tar)
            duration = max(0.001, time.time() - start_time)
            return {
                "direction": "download_dir",
                "remote_dir": remote_dir,
                "local_dir": abs_dest,
                "size_bytes": tar_size,
                "duration_seconds": round(duration, 3),
            }
        finally:
            if os.path.exists(temp_tar):
                os.remove(temp_tar)

    def sync_dir(
        self,
        local_dir: str,
        remote_dir: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delta synchronization using manifest comparison.
        Compares SHA-256 hashes of local files against remote files and only uploads changed files.
        """
        abs_local = os.path.abspath(os.path.expanduser(local_dir))
        if not os.path.isdir(abs_local):
            raise FileTransferError(f"Local directory does not exist: {local_dir}")

        local_manifest = self.build_local_manifest(abs_local)
        remote_manifest = self.build_remote_manifest(remote_dir)

        to_upload = []
        unchanged = []

        for rel_path, l_meta in local_manifest.items():
            r_meta = remote_manifest.get(rel_path)
            if not r_meta or r_meta.get("sha256") != l_meta.get("sha256"):
                to_upload.append(rel_path)
            else:
                unchanged.append(rel_path)

        uploaded_files = []
        total_bytes = 0
        if not dry_run:
            for rel in to_upload:
                src_file = os.path.join(abs_local, rel)
                dst_file = os.path.join(remote_dir, rel)
                summary = self.upload_file(src_file, dst_file, verify_checksum=True)
                uploaded_files.append(summary)
                total_bytes += summary.get("size_bytes", 0)

        return {
            "dry_run": dry_run,
            "local_dir": abs_local,
            "remote_dir": remote_dir,
            "to_upload_count": len(to_upload),
            "unchanged_count": len(unchanged),
            "uploaded_bytes": total_bytes,
            "files_synced": to_upload,
        }

    def build_local_manifest(self, root_dir: str) -> Dict[str, Dict[str, Any]]:
        """Build manifest of all local files with relative path, size, and sha256."""
        manifest = {}
        for root, _, files in os.walk(root_dir):
            for f in files:
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, root_dir)
                try:
                    manifest[rel_p] = {
                        "size": os.path.getsize(full_p),
                        "sha256": calculate_local_sha256(full_p),
                    }
                except Exception:
                    pass
        return manifest

    def build_remote_manifest(self, remote_dir: str) -> Dict[str, Dict[str, Any]]:
        """Query remote directory file manifest from pod."""
        py_code = (
            f"import os, hashlib, json\n"
            f"root = {json.dumps(remote_dir)}\n"
            f"data = {{}}\n"
            f"if os.path.exists(root):\n"
            f"    for r, _, files in os.walk(root):\n"
            f"        for f in files:\n"
            f"            p = os.path.join(r, f)\n"
            f"            rel = os.path.relpath(p, root)\n"
            f"            try:\n"
            f"                with open(p, 'rb') as fp:\n"
            f"                    h = hashlib.sha256(fp.read()).hexdigest()\n"
            f"                data[rel] = {{'size': os.path.getsize(p), 'sha256': h}}\n"
            f"            except Exception: pass\n"
            f"print('___MANIFEST___' + json.dumps(data))\n"
        )
        b64 = base64.b64encode(py_code.encode("utf-8")).decode("utf-8")
        out = self.session.execute_command(f"echo {b64} | base64 -d | python3", timeout=20.0)
        idx = out.find("___MANIFEST___")
        if idx != -1:
            try:
                raw_json = out[idx + len("___MANIFEST___"):].strip().split("\n")[0]
                return json.loads(raw_json)
            except Exception:
                pass
        return {}

    def get_remote_file_sha256(self, remote_path: str) -> Optional[str]:
        """Compute SHA-256 hash of a single file on the remote pod."""
        out = self.session.execute_command(f"sha256sum {shlex.quote(remote_path)} 2>/dev/null || true", timeout=12.0)
        parts = out.strip().split()
        if parts and len(parts[0]) == 64:
            return parts[0]
        return None

    def _is_remote_dir(self, remote_path: str) -> bool:
        """Check if remote path is an existing directory."""
        res = self.session.execute_command(f"[ -d {shlex.quote(remote_path)} ] && echo 1 || echo 0", timeout=8.0)
        return res.strip() == "1"
