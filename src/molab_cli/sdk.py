"""
High-Level Typed Python SDK for MoLab Cloud GPU Orchestration.
Reuses the exact same core execution, transfer, capability, and job subsystems.
"""

from typing import Any, Dict, List, Optional, Union

from molab_cli.backend import MarimoBackendClient
from molab_cli.capabilities import discover_capabilities, run_doctor
from molab_cli.client import MoLabClient
from molab_cli.execution import ExecutionResult, RemoteExecutor
from molab_cli.jobs import JobManager
from molab_cli.keepalive import KeepaliveManager
from molab_cli.sandbox import SandboxSession
from molab_cli.scheduler import BatchOrchestrator, ValidationResult
from molab_cli.services import ServiceManager
from molab_cli.snapshots import SnapshotManager
from molab_cli.storage import StorageBridge
from molab_cli.transfer import TransferManager
from molab_cli.workloads import WorkloadRegistry


class Pod:
    """Represents a remote MoLab GPU/CPU sandbox pod."""

    def __init__(self, notebook_id: str, client: Optional[MoLabClient] = None, job_manager: Optional[JobManager] = None):
        self.notebook_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        self.session = SandboxSession(self.notebook_id, client=client)
        self.executor = RemoteExecutor(self.session)
        self.transfer = TransferManager(self.session)
        self.services = ServiceManager(self.session)
        self._job_manager = job_manager or JobManager()
        self._backend: Optional[MarimoBackendClient] = None

    def resolve(self) -> None:
        self.session.resolve()

    @property
    def sandbox_id(self) -> Optional[str]:
        self.session.resolve()
        return self.session.sandbox_id

    def telemetry(self) -> Dict[str, Any]:
        """Query real-time NVIDIA GPU specs and VRAM allocation."""
        return self.session.get_gpu_telemetry()

    def workload_status(self) -> Dict[str, Any]:
        """Audit active processes and determine occupied vs free status."""
        return self.session.get_workload_status()

    def execute(
        self,
        cmd: str,
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> ExecutionResult:
        """Run a command synchronously on the pod."""
        return self.executor.execute(cmd=cmd, workdir=workdir, env=env, timeout=timeout)

    def push(
        self,
        local_path: str,
        remote_path: Optional[str] = None,
        recursive: bool = False,
    ) -> Dict[str, Any]:
        """Upload a file or folder using native HTTP streaming."""
        if recursive:
            return self.transfer.upload_dir(local_path, remote_path)
        return self.transfer.upload_file(local_path, remote_path, verify_checksum=True)

    def pull(
        self,
        remote_path: str,
        local_path: Optional[str] = None,
        recursive: bool = False,
    ) -> Dict[str, Any]:
        """Download a file or folder using native HTTP streaming."""
        if recursive:
            return self.transfer.download_dir(remote_path, local_path)
        return self.transfer.download_file(remote_path, local_path, verify_checksum=True)

    def sync(
        self,
        local_dir: str,
        remote_dir: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Delta synchronize local directory to pod using SHA-256 manifests."""
        return self.transfer.sync_dir(local_dir, remote_dir, dry_run=dry_run)

    def submit_job(
        self,
        command: str,
        name: Optional[str] = None,
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Submit background job tracked in SQLite."""
        return self._job_manager.submit_job(
            notebook_id=self.notebook_id,
            command=command,
            name=name,
            workdir=workdir,
            env=env,
        )

    def service_status(self, port: int = 8000) -> Dict[str, Any]:
        """Check application health of model server."""
        return self.services.get_service_status(port=port)

    def start_service(self, model: str = "gemma-3-27b", port: int = 8000) -> Dict[str, Any]:
        """Start and verify model server."""
        return self.services.start_model_service(model=model, port=port)

    # -------------------------------------------------------------------------
    # Native Marimo Backend Subsystem
    # -------------------------------------------------------------------------

    @property
    def backend(self) -> MarimoBackendClient:
        """High-speed native REST & WebSocket Marimo backend client."""
        if self._backend is None:
            self._backend = MarimoBackendClient(self.session)
        return self._backend

    def usage(self) -> Dict[str, Any]:
        """Query real-time host RAM, server RAM, kernel RAM, and GPU memory."""
        return self.backend.get_usage()

    def export(
        self,
        format_type: str = "html",
        file_key: str = "notebook.py",
        include_code: bool = True,
    ) -> Union[str, bytes]:
        """Export reactive notebook to HTML, Markdown, IPYNB, Script, or PDF via remote Marimo backend."""
        return self.backend.export_notebook(
            format_type=format_type, file_key=file_key, include_code=include_code
        )

    def eval(
        self,
        code: str,
        file_key: str = "notebook.py",
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Execute Python code directly in remote Marimo kernel without terminal PTY."""
        return self.backend.eval_python(code=code, file_key=file_key, timeout=timeout)

    def kernel_status(self, file_key: str = "notebook.py") -> Dict[str, Any]:
        """Get kernel running/idle state."""
        return self.backend.get_kernel_status(file_key=file_key)

    def restart_kernel(self, file_key: str = "notebook.py") -> bool:
        """Soft-restart remote Python kernel."""
        return self.backend.restart_kernel(file_key=file_key)

    def interrupt_kernel(self, file_key: str = "notebook.py") -> bool:
        """Interrupt active execution in remote kernel."""
        return self.backend.interrupt_kernel(file_key=file_key)

    def list_remote_files(self, path: str = ".") -> List[Dict[str, Any]]:
        """List files and folders directly via native REST API."""
        return self.backend.list_files(path=path)

    def read_remote_file(self, path: str) -> str:
        """Read remote file contents via native REST API."""
        return self.backend.read_file(path=path)

    def update_remote_file(self, path: str, contents: str) -> bool:
        """Update remote file contents via native REST API."""
        return self.backend.update_file(path=path, contents=contents)

    def search_remote_files(
        self,
        query: str,
        path: Optional[str] = None,
        depth: int = 5,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search files on pod via native REST API."""
        return self.backend.search_files(query=query, path=path, depth=depth, limit=limit)

    def list_packages(self) -> List[Dict[str, Any]]:
        """List installed packages via native REST API."""
        return self.backend.list_packages()

    def add_package(self, package_name: str, upgrade: bool = False) -> Dict[str, Any]:
        """Install package via native REST API."""
        return self.backend.add_package(package_name=package_name, upgrade=upgrade)

    # -------------------------------------------------------------------------
    # Snapshot & Persistence Subsystem
    # -------------------------------------------------------------------------

    def create_snapshot(
        self,
        name: Optional[str] = None,
        remote_path: str = "/workspace",
        excludes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a compressed snapshot of pod workspace and save to local storage."""
        sm = SnapshotManager(self._job_manager.db_path)
        return sm.create_snapshot(self.notebook_id, name=name, remote_path=remote_path, excludes=excludes)

    def restore_snapshot(
        self,
        snapshot_id: Optional[str] = None,
        remote_path: str = "/workspace",
    ) -> Dict[str, Any]:
        """Restore a snapshot into the remote pod's /workspace directory."""
        sm = SnapshotManager(self._job_manager.db_path)
        return sm.restore_snapshot(self.notebook_id, snapshot_id=snapshot_id, remote_path=remote_path)

    # -------------------------------------------------------------------------
    # Keepalive Subsystem
    # -------------------------------------------------------------------------

    def start_keepalive(
        self,
        interval: int = 120,
        max_hours: Optional[float] = None,
        auto_restore: bool = True,
    ) -> Dict[str, Any]:
        """Start anti-idle background heartbeat daemon."""
        km = KeepaliveManager(self._job_manager.db_path)
        return km.start_daemon(self.notebook_id, interval=interval, max_hours=max_hours, auto_restore=auto_restore)

    def stop_keepalive(self) -> bool:
        """Stop anti-idle keepalive daemon."""
        km = KeepaliveManager(self._job_manager.db_path)
        return km.stop_daemon(self.notebook_id)

    def keepalive_status(self) -> Optional[Dict[str, Any]]:
        """Inspect keepalive daemon status."""
        km = KeepaliveManager(self._job_manager.db_path)
        return km.get_status(self.notebook_id)

    # -------------------------------------------------------------------------
    # Cloud Storage Bridge Subsystem
    # -------------------------------------------------------------------------

    @property
    def storage(self) -> StorageBridge:
        """Access pod cloud storage bridge (rclone, Hugging Face, git)."""
        return StorageBridge(self.session)


class MoLabSDK:
    """Unified Python SDK entry point for MoLab orchestration."""

    def __init__(self, client: Optional[MoLabClient] = None, db_path: Optional[str] = None):
        self.client = client or MoLabClient()
        self.job_manager = JobManager(db_path=db_path)
        self.snapshot_manager = SnapshotManager(db_path=db_path)
        self.keepalive_manager = KeepaliveManager(db_path=db_path)
        self.workload_registry = WorkloadRegistry()
        self.orchestrator = BatchOrchestrator(
            client=self.client,
            job_manager=self.job_manager,
            workload_registry=self.workload_registry,
        )

    def doctor(self) -> Dict[str, Any]:
        """Run system diagnostics."""
        return run_doctor(self.client)

    def capabilities(self) -> Dict[str, Any]:
        """Query platform and environment capabilities."""
        return discover_capabilities(self.client)

    def list_pods(self) -> List[Dict[str, Any]]:
        """List all notebooks with running status."""
        notebooks = self.client.list_notebooks()
        running = set(self.client.list_running_sandboxes())
        for nb in notebooks:
            nb["is_running"] = nb["id"] in running
        return notebooks

    def get_free_pod(self) -> Optional[Pod]:
        """Discover and return an idle 96GB Blackwell GPU pod."""
        running = self.client.list_running_sandboxes()
        notebooks = {nb["id"]: nb for nb in self.client.list_notebooks()}

        for nb_id in running:
            nb = notebooks.get(nb_id, {})
            if nb.get("gpu") == "rtxp6000":
                pod = Pod(nb_id, client=self.client, job_manager=self.job_manager)
                status = pod.workload_status()
                if not status.get("is_occupied"):
                    return pod
        return None

    def pod(self, notebook_id: str) -> Pod:
        """Get Pod controller for a specific notebook ID."""
        return Pod(notebook_id, client=self.client, job_manager=self.job_manager)

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List historic and active background jobs."""
        return self.job_manager.list_jobs(limit=limit)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Get refreshed job state."""
        return self.job_manager.refresh_job_status(job_id)

    def get_job_logs(self, job_id: str, tail_lines: int = 100) -> str:
        """Get logs for a background job."""
        return self.job_manager.get_job_logs(job_id, tail_lines=tail_lines)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running job."""
        return self.job_manager.cancel_job(job_id)

    # -------------------------------------------------------------------------
    # Batch Orchestration Subsystem
    # -------------------------------------------------------------------------

    def validate_batch(self, manifest: Dict[str, Any]) -> ValidationResult:
        """Validate batch workload manifest schema and dependency DAG."""
        return self.orchestrator.validate(manifest)

    def submit_batch(self, manifest: Dict[str, Any], concurrency_limit: Optional[int] = None) -> str:
        """Validate and submit batch workload manifest for execution."""
        return self.orchestrator.submit(manifest, concurrency_limit=concurrency_limit)

    def run_batch(
        self,
        batch_id: str,
        max_parallel: Optional[int] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 3.0,
    ) -> Dict[str, Any]:
        """Execute a submitted batch pipeline across candidate pods."""
        return self.orchestrator.run_batch(
            batch_id,
            max_parallel=max_parallel,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    def get_batch(self, batch_id: str) -> Dict[str, Any]:
        """Retrieve batch state and tasks list."""
        return self.job_manager.get_batch(batch_id)

    def list_batches(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List historic and active batches."""
        return self.job_manager.list_batches(limit=limit, status=status)

    def cancel_batch(self, batch_id: str) -> Dict[str, Any]:
        """Cancel a running batch."""
        return self.orchestrator.job_manager.cancel_batch(batch_id)

    def retry_batch(self, batch_id: str) -> Dict[str, Any]:
        """Retry failed or skipped tasks in a batch."""
        return self.orchestrator.job_manager.retry_batch(batch_id)

    # -------------------------------------------------------------------------
    # Snapshot & Keepalive Platform Querying
    # -------------------------------------------------------------------------

    def list_snapshots(self, notebook_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """List saved workspace snapshots."""
        return self.snapshot_manager.list_snapshots(notebook_id=notebook_id, limit=limit)

    def list_keepalives(self) -> List[Dict[str, Any]]:
        """List all active or historic keepalive daemons."""
        return self.keepalive_manager.list_keepalives()

