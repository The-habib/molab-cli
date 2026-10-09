"""
High-Level Typed Python SDK for MoLab Cloud GPU Orchestration.
Reuses the exact same core execution, transfer, capability, and job subsystems.
"""

from typing import Any, Dict, List, Optional

from molab_cli.capabilities import discover_capabilities, run_doctor
from molab_cli.client import MoLabClient
from molab_cli.execution import ExecutionResult, RemoteExecutor
from molab_cli.jobs import JobManager
from molab_cli.sandbox import SandboxSession
from molab_cli.scheduler import BatchOrchestrator, ValidationResult
from molab_cli.services import ServiceManager
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


class MoLabSDK:
    """Unified Python SDK entry point for MoLab orchestration."""

    def __init__(self, client: Optional[MoLabClient] = None, db_path: Optional[str] = None):
        self.client = client or MoLabClient()
        self.job_manager = JobManager(db_path=db_path)
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

