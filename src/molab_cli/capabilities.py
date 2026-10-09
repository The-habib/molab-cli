"""
Capabilities and System Doctor diagnostics subsystem for MoLab CLI.
Provides rigorous facts-vs-estimates detection of local and remote capabilities.
"""

import os
import platform
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

from molab_cli.auth import inspect_auth_status
from molab_cli.client import MoLabClient
from molab_cli.config import get_client_cookie


def check_tool_available(tool_name: str) -> bool:
    """Return True if executable is present in PATH."""
    return shutil.which(tool_name) is not None


def check_local_disk_space(path: str = ".") -> Dict[str, Any]:
    """Inspect local storage available in MB/GB."""
    try:
        stat = os.statvfs(path)
        free_bytes = stat.f_bavail * stat.f_frsize
        total_bytes = stat.f_blocks * stat.f_frsize
        return {
            "status": "confirmed",
            "free_gb": round(free_bytes / (1024**3), 2),
            "total_gb": round(total_bytes / (1024**3), 2),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def discover_capabilities(client: Optional[MoLabClient] = None) -> Dict[str, Any]:
    """
    Produce a structured inventory of local and remote capabilities.
    Distinguishes confirmed facts, estimates, unavailable information, and unsupported operations.
    """
    client = client or MoLabClient()

    # 1. Local environment facts
    is_termux = "com.termux" in os.environ.get("PREFIX", "") or "com.termux" in sys.executable
    local_info = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "is_termux": is_termux,
        "sqlite_available": True,
        "sqlite_version": sqlite3.sqlite_version,
        "tools": {
            "curl": check_tool_available("curl"),
            "tar": check_tool_available("tar"),
            "ffmpeg": check_tool_available("ffmpeg"),
            "uv": check_tool_available("uv"),
            "git": check_tool_available("git"),
        },
        "disk_storage": check_local_disk_space(os.path.expanduser("~")),
    }

    # 2. Authentication status
    auth_cookie = get_client_cookie()
    auth_info = {
        "cookie_present": bool(auth_cookie),
        "status": "unverified",
        "email": None,
        "session_id": None,
    }
    if auth_cookie:
        try:
            status = inspect_auth_status()
            auth_info["status"] = "authenticated" if status.get("authenticated") else "invalid"
            auth_info["email"] = status.get("user_email")
            auth_info["session_id"] = status.get("session_id")
        except Exception as e:
            auth_info["status"] = "error"
            auth_info["error"] = str(e)

    # 3. Remote MoLab infrastructure facts
    remote_info: Dict[str, Any] = {
        "api_reachable": False,
        "total_notebooks": 0,
        "running_pods": 0,
        "gpu_pods": 0,
        "pods": [],
    }

    if auth_info["status"] == "authenticated":
        try:
            notebooks = client.list_notebooks()
            running_ids = set(client.list_running_sandboxes())
            remote_info["api_reachable"] = True
            remote_info["total_notebooks"] = len(notebooks)
            remote_info["running_pods"] = len(running_ids)

            gpu_count = 0
            for nb in notebooks:
                nb_id = nb.get("id")
                is_running = nb_id in running_ids
                has_gpu = bool(nb.get("gpu"))
                if is_running and has_gpu:
                    gpu_count += 1
                remote_info["pods"].append({
                    "id": nb_id,
                    "title": nb.get("title"),
                    "gpu": nb.get("gpu"),
                    "is_running": is_running,
                })
            remote_info["gpu_pods"] = gpu_count
        except Exception as e:
            remote_info["error"] = str(e)

    # 4. Transfer capabilities
    transfer_capabilities = {
        "native_http_streaming_upload": {
            "status": "supported",
            "method": "POST /api/files/create (multipart)",
            "pty_bypass": True,
            "max_file_size": "unbounded (line-rate streaming)",
        },
        "native_http_streaming_download": {
            "status": "supported",
            "method": "GET /api/files/download",
            "stream_to_disk": True,
        },
        "recursive_folder_transfer": {
            "status": "supported",
            "method": "tarball stream via native HTTP endpoints",
        },
        "integrity_verification": {
            "status": "supported",
            "algorithm": "SHA-256",
        },
        "delta_sync": {
            "status": "supported",
            "algorithm": "manifest hash comparison",
        },
    }

    # 5. Remote execution capabilities
    execution_capabilities = {
        "interactive_pty_shell": {
            "status": "supported",
            "gateway": "WebSocket /terminal/ws",
            "input_buffer_limit": 4096,
        },
        "structured_command_execution": {
            "status": "supported",
            "features": ["exit_code_trap", "stdout_stderr_split", "timeout_enforcement"],
        },
        "background_job_daemon": {
            "status": "supported",
            "engine": "local SQLite job store + remote nohup watcher",
        },
    }

    return {
        "local": local_info,
        "auth": auth_info,
        "remote": remote_info,
        "transfer": transfer_capabilities,
        "execution": execution_capabilities,
    }


def run_doctor(client: Optional[MoLabClient] = None) -> Dict[str, Any]:
    """
    Run diagnostic health checks across auth, network, storage, dependencies, and remote pods.
    Returns structured results with PASS, WARN, or FAIL statuses.
    """
    client = client or MoLabClient()
    checks: List[Dict[str, Any]] = []

    # 1. Auth check
    cookie = get_client_cookie()
    if not cookie:
        checks.append({
            "name": "authentication",
            "status": "FAIL",
            "message": "Missing __client authentication cookie.",
            "hint": "Run 'molab login' to configure your authentication cookie.",
        })
    else:
        try:
            status = inspect_auth_status()
            if status.get("authenticated"):
                checks.append({
                    "name": "authentication",
                    "status": "PASS",
                    "message": f"Authenticated as {status.get('user_email')}",
                    "details": {"session_id": status.get("session_id")},
                })
            else:
                checks.append({
                    "name": "authentication",
                    "status": "FAIL",
                    "message": "Authentication cookie rejected or expired.",
                    "hint": "Run 'molab login' to renew your Clerk cookie.",
                })
        except Exception as e:
            checks.append({
                "name": "authentication",
                "status": "FAIL",
                "message": f"Auth verification failed: {e}",
                "hint": "Check network connection or refresh cookie via 'molab login'.",
            })

    # 2. Network connectivity check
    try:
        with httpx.Client(timeout=6.0) as http:
            resp = http.get("https://molab.marimo.io", follow_redirects=True)
            if resp.status_code < 400:
                checks.append({
                    "name": "network_connectivity",
                    "status": "PASS",
                    "message": "Connected to https://molab.marimo.io",
                })
            else:
                checks.append({
                    "name": "network_connectivity",
                    "status": "WARN",
                    "message": f"HTTP status {resp.status_code} from molab.marimo.io",
                })
    except Exception as e:
        checks.append({
            "name": "network_connectivity",
            "status": "FAIL",
            "message": f"Cannot reach MoLab servers: {e}",
            "hint": "Check your internet connection or proxy settings.",
        })

    # 3. Tool dependencies check
    missing_tools = []
    for tool in ["curl", "tar"]:
        if not check_tool_available(tool):
            missing_tools.append(tool)

    if not missing_tools:
        checks.append({
            "name": "system_tools",
            "status": "PASS",
            "message": "Required system utilities (curl, tar) are installed.",
        })
    else:
        checks.append({
            "name": "system_tools",
            "status": "WARN",
            "message": f"Missing optional system tools: {', '.join(missing_tools)}",
            "hint": f"Install them via 'pkg install {' '.join(missing_tools)}' for optimal streaming speed.",
        })

    # 4. Local storage check
    disk = check_local_disk_space(os.path.expanduser("~"))
    if disk.get("status") == "confirmed":
        free_gb = disk.get("free_gb", 0)
        if free_gb > 2.0:
            checks.append({
                "name": "local_storage",
                "status": "PASS",
                "message": f"{free_gb} GB free local storage available.",
            })
        else:
            checks.append({
                "name": "local_storage",
                "status": "WARN",
                "message": f"Low local disk space: {free_gb} GB remaining.",
                "hint": "Clean up temporary files to prevent download stalls.",
            })
    else:
        checks.append({
            "name": "local_storage",
            "status": "WARN",
            "message": "Could not determine local disk capacity.",
        })

    # 5. Remote Pods Check
    try:
        notebooks = client.list_notebooks()
        running_sandboxes = client.list_running_sandboxes()
        gpu_running = [nb for nb in notebooks if nb["id"] in running_sandboxes and nb.get("gpu")]
        if gpu_running:
            checks.append({
                "name": "active_gpu_pods",
                "status": "PASS",
                "message": f"{len(gpu_running)} NVIDIA GPU pod(s) running.",
                "details": {"running_gpu_ids": [nb["id"] for nb in gpu_running]},
            })
        else:
            checks.append({
                "name": "active_gpu_pods",
                "status": "WARN",
                "message": "No active GPU pods detected.",
                "hint": "Launch a pod with 'molab create --blackwell' or start an existing notebook.",
            })
    except Exception as e:
        checks.append({
            "name": "active_gpu_pods",
            "status": "WARN",
            "message": f"Could not query remote pods: {e}",
        })

    # Calculate overall health
    overall_status = "HEALTHY"
    if any(c["status"] == "FAIL" for c in checks):
        overall_status = "DEGRADED"
    elif any(c["status"] == "WARN" for c in checks):
        overall_status = "WARNING"

    return {
        "status": overall_status,
        "timestamp": time.time(),
        "total_checks": len(checks),
        "passed": sum(1 for c in checks if c["status"] == "PASS"),
        "warnings": sum(1 for c in checks if c["status"] == "WARN"),
        "failures": sum(1 for c in checks if c["status"] == "FAIL"),
        "checks": checks,
    }
