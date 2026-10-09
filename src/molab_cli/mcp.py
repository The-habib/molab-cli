"""
Model Context Protocol (MCP) Server for MoLab Cloud GPU Orchestration.
Implements the JSON-RPC 2.0 MCP stdio specification (2024-11-05).
Exposes typed tools for Claude, Cursor, Antigravity, and AI agents.
"""

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from molab_cli.backend import MarimoBackendClient
from molab_cli.capabilities import discover_capabilities, run_doctor
from molab_cli.client import MoLabClient
from molab_cli.execution import RemoteExecutor
from molab_cli.jobs import JobManager
from molab_cli.keepalive import KeepaliveManager
from molab_cli.sandbox import SandboxSession
from molab_cli.scheduler import BatchOrchestrator
from molab_cli.services import ServiceManager
from molab_cli.snapshots import SnapshotManager
from molab_cli.storage import StorageBridge
from molab_cli.transfer import TransferManager
from molab_cli.workloads import WorkloadRegistry


class MoLabMCPServer:
    """Stdio-based JSON-RPC 2.0 Model Context Protocol Server."""

    def __init__(self):
        self.client = MoLabClient()
        self.job_manager = JobManager()
        self.workload_registry = WorkloadRegistry()
        self.orchestrator = BatchOrchestrator(
            client=self.client,
            job_manager=self.job_manager,
            workload_registry=self.workload_registry,
        )

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return MCP compliant tool specifications."""
        return [
            {
                "name": "molab_doctor",
                "description": "Run diagnostic health checks on authentication, network connectivity, storage, and active Blackwell GPU pods.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "molab_capabilities",
                "description": "Discover confirmed facts regarding local environment, remote compute capacity, streaming endpoints, and supported runtimes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "molab_list_pods",
                "description": "List all cloud notebooks and active CoreWeave GPU sandboxes in your MoLab workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "molab_get_free_pod",
                "description": "Audit all running Blackwell 96GB pods and return the recommended idle/free pod ID without disrupting existing workloads.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "molab_gpu_telemetry",
                "description": "Query real-time NVIDIA Blackwell GPU telemetry (allocated VRAM, temperature, compute capability, SM count) for a pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "The notebook identifier (e.g. nb_xxx)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_execute",
                "description": "Execute a structured shell command on an active pod with working directory, environment variables, and exit code trapping.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "command": {"type": "string", "description": "Bash command to execute"},
                        "workdir": {"type": "string", "description": "Working directory (default: /workspace)"},
                        "timeout": {"type": "number", "description": "Execution timeout in seconds (default: 60.0)"},
                    },
                    "required": ["notebook_id", "command"],
                },
            },
            {
                "name": "molab_push_file",
                "description": "Upload a local file or directory directly to a pod using high-speed native Marimo HTTP streaming (bypasses terminal PTY buffer).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "local_path": {"type": "string", "description": "Path to local file or folder on phone/host"},
                        "remote_path": {"type": "string", "description": "Destination path on pod (e.g. /workspace/input.mp4)"},
                        "recursive": {"type": "boolean", "description": "Set true to upload a whole directory recursively"},
                    },
                    "required": ["notebook_id", "local_path"],
                },
            },
            {
                "name": "molab_pull_file",
                "description": "Download a processed file or directory from a pod directly to local disk using native HTTP streaming.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "remote_path": {"type": "string", "description": "Remote file or directory path on pod"},
                        "local_path": {"type": "string", "description": "Destination local path"},
                        "recursive": {"type": "boolean", "description": "Set true if remote path is a directory"},
                    },
                    "required": ["notebook_id", "remote_path"],
                },
            },
            {
                "name": "molab_sync_directory",
                "description": "Delta synchronize a local directory to a pod by comparing SHA-256 file manifests and only transferring changed files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "local_dir": {"type": "string", "description": "Local source directory path"},
                        "remote_dir": {"type": "string", "description": "Remote destination directory path"},
                        "dry_run": {"type": "boolean", "description": "Simulate diff without transferring files"},
                    },
                    "required": ["notebook_id", "local_dir", "remote_dir"],
                },
            },
            {
                "name": "molab_job_submit",
                "description": "Submit a detached background job with persistent SQLite tracking, auto-log recording, and artifact collection.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "command": {"type": "string", "description": "Command to run in background"},
                        "name": {"type": "string", "description": "Descriptive job name"},
                        "workdir": {"type": "string", "description": "Working directory (default: /workspace)"},
                    },
                    "required": ["notebook_id", "command"],
                },
            },
            {
                "name": "molab_job_status",
                "description": "Check status of a background job. Synchronizes controller state with remote pod process and detects completion or crash.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job identifier (e.g. job_xxx)"},
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "molab_job_logs",
                "description": "Retrieve trailing execution logs for a background job.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job identifier"},
                        "tail_lines": {"type": "integer", "description": "Number of lines to tail (default: 100)"},
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "molab_job_cancel",
                "description": "Cancel a running background job on the remote pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job identifier"},
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "molab_service_status",
                "description": "Verify application-level health and port binding for an OpenAI-compatible model server running on a pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "port": {"type": "integer", "description": "Port to inspect (default: 8000)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_run_workload",
                "description": "Launch a pre-configured AI workload template (video-enhance-4k, whisper-transcribe, vllm-serve) with validated resource requirements.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workload_name": {"type": "string", "description": "Template name (e.g. video-enhance-4k)"},
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "params": {"type": "object", "description": "Input parameters for the workload template"},
                    },
                    "required": ["workload_name", "notebook_id", "params"],
                },
            },
            {
                "name": "molab_batch_validate",
                "description": "Validate a batch workload pipeline manifest (JSON schema and DAG dependencies).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "manifest": {"type": "object", "description": "Manifest JSON object"},
                        "manifest_path": {"type": "string", "description": "Path to batch manifest JSON file"},
                    },
                },
            },
            {
                "name": "molab_batch_submit",
                "description": "Submit a multi-task batch pipeline manifest for autonomous multi-pod scheduling.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "manifest": {"type": "object", "description": "Manifest JSON object"},
                        "manifest_path": {"type": "string", "description": "Path to batch manifest JSON file"},
                        "concurrency_limit": {"type": "integer", "description": "Max parallel tasks across pods"},
                    },
                },
            },
            {
                "name": "molab_batch_status",
                "description": "Query batch status, progress counts, and per-task state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_id": {"type": "string", "description": "Batch pipeline ID (e.g. batch_xxx)"},
                    },
                    "required": ["batch_id"],
                },
            },
            {
                "name": "molab_batch_cancel",
                "description": "Cancel a running batch pipeline and terminate all remote processes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_id": {"type": "string", "description": "Batch pipeline ID to cancel"},
                    },
                    "required": ["batch_id"],
                },
            },
            {
                "name": "molab_usage",
                "description": "Query real-time host RAM, server RAM, kernel RAM, CPU utilization, and GPU memory telemetry directly from remote Marimo backend.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_export_notebook",
                "description": "Export reactive Marimo notebook directly to HTML, Markdown, IPYNB, Script, or PDF via native Marimo server exporter.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "format_type": {
                            "type": "string",
                            "enum": ["html", "markdown", "ipynb", "script", "pdf"],
                            "description": "Export target format (default: html)",
                        },
                        "file_key": {"type": "string", "description": "Notebook filename (default: notebook.py)"},
                        "include_code": {"type": "boolean", "description": "Include source code cells in export (default: true)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_kernel_eval",
                "description": "Execute Python code directly inside the remote Marimo Python kernel without terminal PTY buffers, capturing stdout, stderr, and MIME output.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "code": {"type": "string", "description": "Python code snippet to execute"},
                        "file_key": {"type": "string", "description": "Notebook context filename (default: notebook.py)"},
                        "timeout": {"type": "number", "description": "Evaluation timeout in seconds (default: 30.0)"},
                    },
                    "required": ["notebook_id", "code"],
                },
            },
            {
                "name": "molab_kernel_status",
                "description": "Query running vs idle state of the remote Python kernel on the pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "file_key": {"type": "string", "description": "Notebook filename (default: notebook.py)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_kernel_restart",
                "description": "Soft-restart the remote Marimo Python kernel without terminating the pod container.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "file_key": {"type": "string", "description": "Notebook filename (default: notebook.py)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_kernel_interrupt",
                "description": "Interrupt any active execution in the remote Marimo Python kernel.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "file_key": {"type": "string", "description": "Notebook filename (default: notebook.py)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_file_list",
                "description": "List files and directories directly over native HTTP JSON endpoint on the remote pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "path": {"type": "string", "description": "Path to list (default: .)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_file_details",
                "description": "Fetch metadata, mime type, and readable text contents of a file on the remote pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "path": {"type": "string", "description": "Path to file"},
                    },
                    "required": ["notebook_id", "path"],
                },
            },
            {
                "name": "molab_file_search",
                "description": "Fast server-side recursive file and directory search on the remote pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "query": {"type": "string", "description": "Search pattern or string"},
                        "path": {"type": "string", "description": "Search root directory (default: current directory)"},
                        "depth": {"type": "integer", "description": "Maximum directory search depth (default: 5)"},
                        "limit": {"type": "integer", "description": "Maximum number of matched files (default: 100)"},
                    },
                    "required": ["notebook_id", "query"],
                },
            },
            {
                "name": "molab_pkg_list",
                "description": "List Python packages installed on the remote pod via native Marimo package manager.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_snapshot_create",
                "description": "Create a compressed snapshot of pod workspace and stream to local persistent storage to prevent data loss on pod timeout/reset.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "name": {"type": "string", "description": "Optional name for checkpoint"},
                        "remote_path": {"type": "string", "description": "Remote path to archive (default: /workspace)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_snapshot_restore",
                "description": "Restore a saved workspace snapshot into the remote pod's /workspace directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "snapshot_id": {"type": "string", "description": "Specific snapshot ID (default: latest snapshot)"},
                        "remote_path": {"type": "string", "description": "Destination directory (default: /workspace)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_snapshot_list",
                "description": "List saved workspace snapshots and checkpoints.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Optional notebook ID filter"},
                        "limit": {"type": "integer", "description": "Max snapshots to return (default: 25)"},
                    },
                },
            },
            {
                "name": "molab_keepalive_start",
                "description": "Start an autonomous background anti-idle heartbeat daemon to keep a Blackwell pod alive and auto-restore workspace upon pod resurrection.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "interval": {"type": "integer", "description": "Heartbeat interval in seconds (default: 120)"},
                        "max_hours": {"type": "number", "description": "Max runtime in hours (default: unlimited)"},
                        "auto_restore": {"type": "boolean", "description": "Whether to auto-restore latest snapshot if pod resets (default: true)"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_keepalive_stop",
                "description": "Stop the background anti-idle keepalive daemon for a pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_keepalive_status",
                "description": "Inspect live status of anti-idle keepalive daemon and session TTL for a pod.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                    },
                    "required": ["notebook_id"],
                },
            },
            {
                "name": "molab_storage_backup",
                "description": "Sync pod directory directly to remote cloud storage (S3/R2/B2/GCS) via rclone at multi-gigabit speeds.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "remote_dest": {"type": "string", "description": "Destination remote storage path (e.g. r2:my-bucket/weights)"},
                        "source_dir": {"type": "string", "description": "Source pod directory (default: /workspace)"},
                        "flags": {"type": "string", "description": "Additional rclone flags"},
                    },
                    "required": ["notebook_id", "remote_dest"],
                },
            },
            {
                "name": "molab_storage_restore",
                "description": "Restore pod directory directly from remote cloud storage via rclone at multi-gigabit speeds.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "remote_source": {"type": "string", "description": "Source remote storage path (e.g. r2:my-bucket/weights)"},
                        "target_dir": {"type": "string", "description": "Destination pod directory (default: /workspace)"},
                        "flags": {"type": "string", "description": "Additional rclone flags"},
                    },
                    "required": ["notebook_id", "remote_source"],
                },
            },
            {
                "name": "molab_storage_hf_pull",
                "description": "Download weights/datasets directly from Hugging Face Hub to pod at multi-gigabit speeds.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "notebook_id": {"type": "string", "description": "Target notebook pod ID"},
                        "repo_id": {"type": "string", "description": "Hugging Face repo ID (e.g. google/gemma-3-27b-it)"},
                        "dest": {"type": "string", "description": "Destination directory on pod (default: /workspace)"},
                        "filename": {"type": "string", "description": "Optional specific file to download"},
                        "token": {"type": "string", "description": "Optional Hugging Face user token"},
                    },
                    "required": ["notebook_id", "repo_id"],
                },
            },
        ]

    def execute_tool(self, name: str, args: Dict[str, Any]) -> Any:
        """Route tool invocation to the corresponding subsystem."""
        if name == "molab_doctor":
            return run_doctor(self.client)

        elif name == "molab_capabilities":
            return discover_capabilities(self.client)

        elif name == "molab_list_pods":
            notebooks = self.client.list_notebooks()
            running = set(self.client.list_running_sandboxes())
            return [
                {
                    "id": nb["id"],
                    "title": nb.get("title"),
                    "gpu": nb.get("gpu"),
                    "is_running": nb["id"] in running,
                }
                for nb in notebooks
            ]

        elif name == "molab_get_free_pod":
            running = self.client.list_running_sandboxes()
            notebooks = {nb["id"]: nb for nb in self.client.list_notebooks()}
            recommended = None
            audit = []
            for nb_id in running:
                nb = notebooks.get(nb_id, {})
                if nb.get("gpu") == "rtxp6000":
                    session = SandboxSession(nb_id, client=self.client)
                    status = session.get_workload_status()
                    audit.append(status)
                    if not status.get("is_occupied") and not recommended:
                        recommended = nb_id
            return {"recommended_free_pod": recommended, "pods": audit}

        elif name == "molab_gpu_telemetry":
            session = SandboxSession(args["notebook_id"], client=self.client)
            return session.get_gpu_telemetry()

        elif name == "molab_execute":
            session = SandboxSession(args["notebook_id"], client=self.client)
            executor = RemoteExecutor(session)
            res = executor.execute(
                cmd=args["command"],
                workdir=args.get("workdir"),
                timeout=float(args.get("timeout", 60.0)),
            )
            return res.to_dict()

        elif name == "molab_push_file":
            session = SandboxSession(args["notebook_id"], client=self.client)
            transfer = TransferManager(session)
            if args.get("recursive") or os.path.isdir(args["local_path"]):
                return transfer.upload_dir(args["local_path"], args.get("remote_path"))
            return transfer.upload_file(args["local_path"], args.get("remote_path"), verify_checksum=True)

        elif name == "molab_pull_file":
            session = SandboxSession(args["notebook_id"], client=self.client)
            transfer = TransferManager(session)
            if args.get("recursive"):
                return transfer.download_dir(args["remote_path"], args.get("local_path"))
            return transfer.download_file(args["remote_path"], args.get("local_path"), verify_checksum=True)

        elif name == "molab_sync_directory":
            session = SandboxSession(args["notebook_id"], client=self.client)
            transfer = TransferManager(session)
            return transfer.sync_dir(
                local_dir=args["local_dir"],
                remote_dir=args["remote_dir"],
                dry_run=args.get("dry_run", False),
            )

        elif name == "molab_job_submit":
            return self.job_manager.submit_job(
                notebook_id=args["notebook_id"],
                command=args["command"],
                name=args.get("name"),
                workdir=args.get("workdir"),
            )

        elif name == "molab_job_status":
            return self.job_manager.refresh_job_status(args["job_id"])

        elif name == "molab_job_logs":
            return {"job_id": args["job_id"], "logs": self.job_manager.get_job_logs(args["job_id"], tail_lines=int(args.get("tail_lines", 100)))}

        elif name == "molab_job_cancel":
            return self.job_manager.cancel_job(args["job_id"])

        elif name == "molab_service_status":
            session = SandboxSession(args["notebook_id"], client=self.client)
            sm = ServiceManager(session)
            return sm.get_service_status(port=int(args.get("port", 8000)))

        elif name == "molab_run_workload":
            return self.workload_registry.run_workload(
                name=args["workload_name"],
                notebook_id=args["notebook_id"],
                params=args["params"],
                job_manager=self.job_manager,
            )

        elif name == "molab_batch_validate":
            manifest = args.get("manifest")
            if not manifest and args.get("manifest_path"):
                with open(args["manifest_path"], "r") as f:
                    manifest = json.load(f)
            return self.orchestrator.validate(manifest or {}).to_dict()

        elif name == "molab_batch_submit":
            manifest = args.get("manifest")
            if not manifest and args.get("manifest_path"):
                with open(args["manifest_path"], "r") as f:
                    manifest = json.load(f)
            batch_id = self.orchestrator.submit(
                manifest or {},
                concurrency_limit=args.get("concurrency_limit"),
            )
            return {"batch_id": batch_id, "status": "SUBMITTED"}

        elif name == "molab_batch_status":
            return self.job_manager.get_batch(args["batch_id"])

        elif name == "molab_batch_cancel":
            return self.orchestrator.job_manager.cancel_batch(args["batch_id"])

        elif name == "molab_usage":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.get_usage()

        elif name == "molab_export_notebook":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            fmt = args.get("format_type", "html")
            content = backend.export_notebook(
                format_type=fmt,
                file_key=args.get("file_key", "notebook.py"),
                include_code=args.get("include_code", True),
            )
            is_str = isinstance(content, str)
            return {
                "format": fmt,
                "length": len(content),
                "content": content if is_str else f"<binary {len(content)} bytes>",
            }

        elif name == "molab_kernel_eval":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.eval_python(
                code=args["code"],
                file_key=args.get("file_key", "notebook.py"),
                timeout=float(args.get("timeout", 30.0)),
            )

        elif name == "molab_kernel_status":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.get_kernel_status(file_key=args.get("file_key", "notebook.py"))

        elif name == "molab_kernel_restart":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return {"success": backend.restart_kernel(file_key=args.get("file_key", "notebook.py"))}

        elif name == "molab_kernel_interrupt":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return {"success": backend.interrupt_kernel(file_key=args.get("file_key", "notebook.py"))}

        elif name == "molab_file_list":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.list_files(path=args.get("path", "."))

        elif name == "molab_file_details":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.file_details(path=args["path"])

        elif name == "molab_file_search":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.search_files(
                query=args["query"],
                path=args.get("path"),
                depth=int(args.get("depth", 5)),
                limit=int(args.get("limit", 100)),
            )

        elif name == "molab_pkg_list":
            session = SandboxSession(args["notebook_id"], client=self.client)
            backend = MarimoBackendClient(session)
            return backend.list_packages()

        elif name == "molab_snapshot_create":
            sm = SnapshotManager()
            return sm.create_snapshot(
                notebook_id=args["notebook_id"],
                name=args.get("name"),
                remote_path=args.get("remote_path", "/workspace"),
            )

        elif name == "molab_snapshot_restore":
            sm = SnapshotManager()
            return sm.restore_snapshot(
                notebook_id=args["notebook_id"],
                snapshot_id=args.get("snapshot_id"),
                remote_path=args.get("remote_path", "/workspace"),
            )

        elif name == "molab_snapshot_list":
            sm = SnapshotManager()
            return sm.list_snapshots(
                notebook_id=args.get("notebook_id"),
                limit=int(args.get("limit", 25)),
            )

        elif name == "molab_keepalive_start":
            km = KeepaliveManager()
            return km.start_daemon(
                notebook_id=args["notebook_id"],
                interval=int(args.get("interval", 120)),
                max_hours=float(args["max_hours"]) if args.get("max_hours") is not None else None,
                auto_restore=bool(args.get("auto_restore", True)),
            )

        elif name == "molab_keepalive_stop":
            km = KeepaliveManager()
            return {"success": km.stop_daemon(args["notebook_id"])}

        elif name == "molab_keepalive_status":
            km = KeepaliveManager()
            return km.get_status(args["notebook_id"]) or {"status": "none"}

        elif name == "molab_storage_backup":
            sb = StorageBridge(args["notebook_id"])
            return sb.rclone_sync_to_cloud(
                remote_dest=args["remote_dest"],
                source_dir=args.get("source_dir", "/workspace"),
                extra_flags=args.get("flags"),
            )

        elif name == "molab_storage_restore":
            sb = StorageBridge(args["notebook_id"])
            return sb.rclone_sync_from_cloud(
                remote_source=args["remote_source"],
                target_dir=args.get("target_dir", "/workspace"),
                extra_flags=args.get("flags"),
            )

        elif name == "molab_storage_hf_pull":
            sb = StorageBridge(args["notebook_id"])
            return sb.hf_download(
                repo_id=args["repo_id"],
                dest_dir=args.get("dest", "/workspace"),
                filename=args.get("filename"),
                token=args.get("token"),
            )

        raise ValueError(f"Unknown tool: {name}")

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process an individual JSON-RPC 2.0 request."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "molab-mcp",
                        "version": "2.0.0",
                    },
                },
            }

        elif method == "notifications/initialized":
            return None

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.get_tool_definitions()},
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                result_data = self.execute_tool(tool_name, tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result_data, indent=2)}
                        ],
                        "isError": False,
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Error executing {tool_name}: {e}\n{traceback.format_exc()}"}
                        ],
                        "isError": True,
                    },
                }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

    def run_stdio(self) -> None:
        """Run the JSON-RPC stdio event loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def run_mcp_server() -> None:
    """Entry point for `molab mcp`."""
    server = MoLabMCPServer()
    server.run_stdio()


if __name__ == "__main__":
    run_mcp_server()
