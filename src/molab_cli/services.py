"""
Service Management Subsystem for Model Serving and Application Endpoints on MoLab Pods.
Handles lifecycle, application-level health checks, port bindings, and log inspection.
"""

import json
import time
from typing import Any, Dict, Optional

from molab_cli.exceptions import ServiceError
from molab_cli.execution import RemoteExecutor
from molab_cli.sandbox import SandboxSession


class ServiceManager:
    """Manages remote HTTP model servers and microservices running on MoLab pods."""

    def __init__(self, session: SandboxSession):
        self.session = session
        self.executor = RemoteExecutor(session)

    def get_service_status(self, port: int = 8000) -> Dict[str, Any]:
        """
        Verify real application-level health on the target port inside the pod.
        Does not merely check if process exists; queries /health endpoint.
        """
        self.session.resolve()
        start_time = time.time()
        cmd = f"curl -s -m 4 http://127.0.0.1:{port}/health"
        res = self.executor.execute(cmd, timeout=8.0)
        resp_ms = round((time.time() - start_time) * 1000, 1)

        raw_out = res.stdout.strip()
        is_healthy = False
        health_data = {}

        if "healthy" in raw_out.lower():
            is_healthy = True
            try:
                health_data = json.loads(raw_out)
            except Exception:
                health_data = {"raw": raw_out}

        # Check process listening on port
        port_check = self.executor.execute(f"ss -tulpn | grep ':{port} ' || true", timeout=6.0).stdout.strip()
        has_listener = bool(port_check)

        return {
            "notebook_id": self.session.notebook_id,
            "sandbox_id": self.session.sandbox_id,
            "port": port,
            "has_listener": has_listener,
            "is_healthy": is_healthy,
            "status": "HEALTHY" if is_healthy else ("UNHEALTHY" if has_listener else "STOPPED"),
            "response_time_ms": resp_ms if is_healthy else None,
            "health_details": health_data,
        }

    def start_model_service(
        self,
        model: str = "gemma-3-27b",
        port: int = 8000,
        startup_timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Launch or ensure the model inference server is active and passes /health check.
        """
        status = self.get_service_status(port)
        if status["is_healthy"]:
            return status

        # Trigger startup script on pod
        launch_cmd = (
            "nohup bash -c '"
            "if [ -f /marimo/start_server.sh ]; then bash /marimo/start_server.sh; "
            "elif [ -f /workspace/start.sh ]; then bash /workspace/start.sh; "
            f"else python3 -m vllm.entrypoints.openai.api_server --port {port}; fi"
            "' > /workspace/service.log 2>&1 &"
        )
        self.executor.execute(launch_cmd, timeout=12.0)

        # Poll health endpoint until healthy or timeout
        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            time.sleep(2.5)
            status = self.get_service_status(port)
            if status["is_healthy"]:
                return status

        raise ServiceError(
            f"Model server on pod {self.session.notebook_id}:{port} failed to become healthy within {startup_timeout}s.",
            hint="Check service logs with 'molab serve logs <notebook_id>' for Python/CUDA tracebacks.",
            details={"last_status": status},
        )

    def stop_service(self, port: int = 8000) -> bool:
        """Gracefully terminate any service bound to the given port."""
        self.session.resolve()
        # Find PIDs listening on port
        cmd = f"fuser -k {port}/tcp 2>/dev/null || pkill -f 'server.py|vllm|uvicorn' || true"
        self.executor.execute(cmd, timeout=10.0)
        time.sleep(1.0)
        status = self.get_service_status(port)
        return not status["has_listener"]

    def get_service_logs(self, tail_lines: int = 50) -> str:
        """Read recent service logs from /workspace/service.log."""
        self.session.resolve()
        cmd = f"tail -n {int(tail_lines)} /workspace/service.log 2>/dev/null || tail -n {int(tail_lines)} /marimo/server.log 2>/dev/null || echo '[No service log found]'"
        res = self.executor.execute(cmd, timeout=10.0)
        return res.stdout
