"""
Cloud Storage Bridge for MoLab Compute Pods.
Leverages pod pre-installed utilities (rclone, huggingface-cli, git) for direct
multi-gigabit cloud persistence without routing heavy weights or datasets through local mobile storage.
"""

import os
import shlex
from typing import Any, Dict, List, Optional

from molab_cli.exceptions import MoLabError
from molab_cli.notifications import redact_sensitive_data
from molab_cli.sandbox import SandboxSession


class StorageBridgeError(MoLabError):
    """Raised when cloud storage operations fail."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="STORAGE_BRIDGE_ERROR", details=details)


class StorageBridge:
    """Orchestrates cloud storage synchronization directly on MoLab compute pods."""

    def __init__(self, session_or_notebook_id: Any):
        if isinstance(session_or_notebook_id, str):
            self.session = SandboxSession(session_or_notebook_id)
        else:
            self.session = session_or_notebook_id

    def _sanitize_rclone_flags(self, extra_flags: Optional[str]) -> str:
        if not extra_flags:
            return "-v --transfers 8 --checkers 16"
        for char in [";", "&", "|", "`", "$", "\n", "\r", ">", "<"]:
            if char in extra_flags:
                raise StorageBridgeError(f"Forbidden characters detected in rclone flags: {char}")
        parts = shlex.split(extra_flags)
        return " ".join(shlex.quote(p) for p in parts)

    def setup_rclone_config(self, local_config_path: Optional[str] = None) -> bool:
        """
        Upload local rclone configuration file (~/.config/rclone/rclone.conf) to the pod.
        Ensures restrictive file permissions (chmod 600) on the pod to protect credentials.
        """
        config_path = local_config_path or os.path.expanduser("~/.config/rclone/rclone.conf")
        if not os.path.exists(config_path):
            raise StorageBridgeError(
                f"Local rclone configuration file not found at: {config_path}. "
                "Run 'rclone config' locally first or specify a valid config path."
            )

        self.session.resolve()
        self.session.execute_command("mkdir -p /root/.config/rclone")
        self.session.push_file(config_path, "/root/.config/rclone/rclone.conf")
        self.session.execute_command("chmod 600 /root/.config/rclone/rclone.conf")
        return True

    def rclone_sync_to_cloud(
        self,
        remote_dest: str,
        source_dir: str = "/workspace",
        extra_flags: Optional[str] = None,
        destructive: bool = False,
        timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        Transfer pod directory to remote cloud storage (S3/R2/B2/GCS) via rclone.
        Non-destructive by default using 'rclone copy' to prevent accidental deletion
        of remote data. Pass destructive=True to use 'rclone sync'.
        """
        self.session.resolve()
        flags = self._sanitize_rclone_flags(extra_flags)
        subcmd = "sync" if destructive else "copy"
        cmd = f"rclone {subcmd} {shlex.quote(source_dir)} {shlex.quote(remote_dest)} {flags}"
        output = self.session.execute_command(cmd, timeout=timeout)
        return {
            "notebook_id": self.session.notebook_id,
            "source_dir": source_dir,
            "remote_dest": remote_dest,
            "destructive": destructive,
            "operation": subcmd,
            "output": redact_sensitive_data(output),
        }

    def rclone_sync_from_cloud(
        self,
        remote_source: str,
        target_dir: str = "/workspace",
        extra_flags: Optional[str] = None,
        destructive: bool = False,
        timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        Restore pod directory from remote cloud storage via rclone.
        Non-destructive by default using 'rclone copy' to protect existing pod files.
        Pass destructive=True to use 'rclone sync'.
        """
        self.session.resolve()
        flags = self._sanitize_rclone_flags(extra_flags)
        subcmd = "sync" if destructive else "copy"
        cmd = f"mkdir -p {shlex.quote(target_dir)} && rclone {subcmd} {shlex.quote(remote_source)} {shlex.quote(target_dir)} {flags}"
        output = self.session.execute_command(cmd, timeout=timeout)
        return {
            "notebook_id": self.session.notebook_id,
            "remote_source": remote_source,
            "target_dir": target_dir,
            "destructive": destructive,
            "operation": subcmd,
            "output": redact_sensitive_data(output),
        }

    def hf_download(
        self,
        repo_id: str,
        dest_dir: str = "/workspace",
        filename: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """
        Download models or datasets directly from Hugging Face Hub to pod /workspace.
        Uses HF_TOKEN environment variable to avoid token exposure in command arguments.
        """
        self.session.resolve()
        cmd_parts = ["huggingface-cli", "download", shlex.quote(repo_id), "--local-dir", shlex.quote(dest_dir)]
        if filename:
            cmd_parts.extend(["--include", shlex.quote(filename)])

        if token:
            cmd = f"HF_TOKEN={shlex.quote(token)} " + " ".join(cmd_parts)
        else:
            cmd = " ".join(cmd_parts)

        output = self.session.execute_command(cmd, timeout=timeout)
        cleaned_output = redact_sensitive_data(output)
        if token:
            cleaned_output = cleaned_output.replace(token, "[REDACTED]")

        return {
            "notebook_id": self.session.notebook_id,
            "repo_id": repo_id,
            "dest_dir": dest_dir,
            "output": cleaned_output,
        }

    def hf_upload(
        self,
        local_pod_path: str,
        repo_id: str,
        repo_type: str = "model",
        token: Optional[str] = None,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """
        Upload weights or outputs directly from pod to Hugging Face Hub.
        Uses HF_TOKEN environment variable to avoid token exposure in command arguments.
        """
        self.session.resolve()
        cmd_parts = [
            "huggingface-cli",
            "upload",
            shlex.quote(repo_id),
            shlex.quote(local_pod_path),
            "--repo-type",
            shlex.quote(repo_type),
        ]

        if token:
            cmd = f"HF_TOKEN={shlex.quote(token)} " + " ".join(cmd_parts)
        else:
            cmd = " ".join(cmd_parts)

        output = self.session.execute_command(cmd, timeout=timeout)
        cleaned_output = redact_sensitive_data(output)
        if token:
            cleaned_output = cleaned_output.replace(token, "[REDACTED]")

        return {
            "notebook_id": self.session.notebook_id,
            "repo_id": repo_id,
            "local_pod_path": local_pod_path,
            "output": cleaned_output,
        }

    def git_clone(
        self,
        repo_url: str,
        dest_dir: Optional[str] = None,
        branch: Optional[str] = None,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """
        Clone a git repository directly onto the pod.
        """
        self.session.resolve()
        cmd_parts = ["git", "clone"]
        if branch:
            cmd_parts.extend(["-b", shlex.quote(branch)])
        cmd_parts.append(shlex.quote(repo_url))
        if dest_dir:
            cmd_parts.append(shlex.quote(dest_dir))

        cmd = " ".join(cmd_parts)
        output = self.session.execute_command(cmd, timeout=timeout)
        return {
            "notebook_id": self.session.notebook_id,
            "repo_url": repo_url,
            "output": output,
        }
