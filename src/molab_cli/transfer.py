"""
Universal Streaming File Transfer, Integrity Verification, and Directory Sync Engine.
Uses native Marimo HTTP REST endpoints (/api/files/create and /api/files/download)
with SHA-256 checksums, chunked streaming, staging validation, and atomic promotion.
"""

import base64
import hashlib
import json
import os
import shlex
import subprocess
import tarfile
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


def safe_extract_tar(tar_path: str, target_dir: str) -> None:
    """
    Safely extract a tar archive preventing directory traversal, absolute paths,
    and unsafe symlinks pointing outside the target extraction root.
    """
    target_abs = os.path.abspath(target_dir)
    os.makedirs(target_abs, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tar:
        for member in tar.getmembers():
            dest = os.path.abspath(os.path.join(target_abs, member.name))
            if not (dest == target_abs or dest.startswith(target_abs + os.sep)):
                raise FileTransferError(
                    f"Refusing to extract path-traversing archive member: {member.name}",
                    details={"member": member.name, "target_dir": target_dir},
                )
            if member.issym() or member.islnk():
                link_target = os.path.abspath(os.path.join(os.path.dirname(dest), member.linkname))
                if not (link_target == target_abs or link_target.startswith(target_abs + os.sep)):
                    raise FileTransferError(
                        f"Refusing to extract unsafe symlink member: {member.name} -> {member.linkname}",
                        details={"member": member.name, "linkname": member.linkname},
                    )
        try:
            tar.extractall(target_abs, filter="data")
        except TypeError:
            tar.extractall(target_abs)


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
        Stages upload into a temporary remote file, verifies size and SHA-256 checksum,
        and atomically promotes to destination. Preserves existing destination on failure.
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

        staging_name = f".tmp_up_{uuid.uuid4().hex[:8]}_{filename}"
        staging_remote = f"{dest_dir}/{staging_name}"

        start_time = time.time()
        local_hash = calculate_local_sha256(abs_local) if verify_checksum else None

        # Execute upload into staging destination using curl streaming
        upload_url = f"{self.session.base_url}/api/files/create?token={self.session.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-X", "POST",
            "--connect-timeout", "20",
            upload_url,
            "-F", f"path={dest_dir}",
            "-F", "type=file",
            "-F", f"name={staging_name}",
            "-F", f"file=@{abs_local}",
        ]

        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Clean up partial staging file if any
            try:
                from molab_cli.backend import MarimoBackendClient
                backend = MarimoBackendClient(self.session)
                backend.delete_file(staging_remote)
            except Exception:
                pass
            raise FileTransferError(
                f"Failed to upload {local_path} to pod staging path: {res.stderr}",
                details={"exit_code": res.returncode, "destination": dest_file},
            )

        duration = max(0.001, time.time() - start_time)
        speed_mb = (file_size / (1024 * 1024)) / duration

        checksum_verified = True
        remote_hash = None
        if verify_checksum:
            remote_hash = self.get_remote_file_sha256(staging_remote)
            if remote_hash != local_hash:
                checksum_verified = False
                # Remove corrupted staging file, preserving original destination
                try:
                    from molab_cli.backend import MarimoBackendClient
                    backend = MarimoBackendClient(self.session)
                    backend.delete_file(staging_remote)
                except Exception:
                    pass
                raise FileTransferError(
                    f"Checksum mismatch for {local_path}: local={local_hash} vs remote={remote_hash}",
                    hint="The upload payload may have been truncated or corrupted during transit. Existing destination was preserved.",
                    details={"local_sha256": local_hash, "remote_sha256": remote_hash},
                )

        # Promote verified staging file to destination
        try:
            from molab_cli.backend import MarimoBackendClient
            backend = MarimoBackendClient(self.session)
            # Remove existing destination file only now that replacement is verified
            backend.delete_file(dest_file)
            backend.move_file(staging_remote, dest_file)
        except Exception as promo_err:
            try:
                self.session.execute_command(f"mv -f {shlex.quote(staging_remote)} {shlex.quote(dest_file)}")
            except Exception:
                raise FileTransferError(
                    f"Failed to promote staged file to destination {dest_file}: {promo_err}",
                    details={"staging_path": staging_remote, "destination": dest_file},
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
        Streams chunked bytes to a temporary staging file, validates checksum,
        and atomically replaces destination. Preserves existing destination on failure.
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
            "--connect-timeout", "20",
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

        if not os.path.exists(temp_dest):
            raise FileTransferError(f"Downloaded temporary file missing: {temp_dest}")

        file_size = os.path.getsize(temp_dest)
        duration = max(0.001, time.time() - start_time)
        speed_mb = (file_size / (1024 * 1024)) / duration

        checksum_verified = True
        local_hash = None
        if verify_checksum:
            local_hash = calculate_local_sha256(temp_dest)
            if remote_hash and local_hash != remote_hash:
                checksum_verified = False
                if os.path.exists(temp_dest):
                    os.remove(temp_dest)
                raise FileTransferError(
                    f"Checksum mismatch for downloaded file {remote_path}: expected {remote_hash}, got {local_hash}. Existing destination preserved.",
                    details={"expected_sha256": remote_hash, "downloaded_sha256": local_hash},
                )

        # Atomic replacement: destination is only replaced after complete verification
        os.replace(temp_dest, abs_dest)

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
                "--connect-timeout", "20",
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
            extract_cmd = (
                f"tar -xzf /tmp/{tar_name} -C $(dirname {shlex.quote(target_remote)}) && rm -f /tmp/{tar_name}"
            )
            self.session.execute_command(extract_cmd)
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
        """Archive remote directory into /tmp on pod and download with safe tar extraction."""
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
            subprocess.run(
                ["curl", "-s", "-f", "-L", "--connect-timeout", "20", "-o", temp_tar, download_url],
                check=True
            )

            os.makedirs(abs_dest, exist_ok=True)
            extract_target = os.path.dirname(abs_dest) if not os.path.isdir(abs_dest) else abs_dest
            safe_extract_tar(temp_tar, extract_target)

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
        """
        Query remote directory file manifest from pod with chunked hashing
        and explicit error handling. Distinguishes missing/empty dirs from failures.
        """
        py_code = (
            f"import os, hashlib, json, sys\n"
            f"root = {json.dumps(remote_dir)}\n"
            f"if not os.path.exists(root):\n"
            f"    print('___MANIFEST_NOT_FOUND___')\n"
            f"    sys.exit(0)\n"
            f"try:\n"
            f"    data = {{}}\n"
            f"    for r, _, files in os.walk(root):\n"
            f"        for f in files:\n"
            f"            p = os.path.join(r, f)\n"
            f"            rel = os.path.relpath(p, root)\n"
            f"            try:\n"
            f"                h = hashlib.sha256()\n"
            f"                with open(p, 'rb') as fp:\n"
            f"                    while chunk := fp.read(65536):\n"
            f"                        h.update(chunk)\n"
            f"                data[rel] = {{'size': os.path.getsize(p), 'sha256': h.hexdigest()}}\n"
            f"            except Exception:\n"
            f"                pass\n"
            f"    print('___MANIFEST___' + json.dumps(data))\n"
            f"except Exception as e:\n"
            f"    print('___MANIFEST_ERROR___' + str(e))\n"
        )
        b64 = base64.b64encode(py_code.encode("utf-8")).decode("utf-8")
        out = self.session.execute_command(f"echo {b64} | base64 -d | python3", timeout=30.0)

        if "___MANIFEST_NOT_FOUND___" in out:
            return {}

        idx = out.find("___MANIFEST___")
        if idx != -1:
            try:
                raw_json = out[idx + len("___MANIFEST___"):].strip().split("\n")[0]
                return json.loads(raw_json)
            except Exception as e:
                raise FileTransferError(f"Malformed manifest JSON from remote pod: {e}")

        err_idx = out.find("___MANIFEST_ERROR___")
        if err_idx != -1:
            err_msg = out[err_idx + len("___MANIFEST_ERROR___"):].strip().split("\n")[0]
            raise FileTransferError(f"Remote error building manifest for {remote_dir}: {err_msg}")

        raise FileTransferError(
            f"Failed to query remote directory manifest for {remote_dir}: {out.strip() or 'Execution failed'}"
        )

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
