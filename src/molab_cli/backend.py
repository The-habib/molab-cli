"""
Native Marimo REST and WebSocket Backend Subsystem.
Directly communicates with the remote Marimo server (port 8080) running on cloud pods.
Exposes native kernel evaluation, notebook export, server-side file management,
package installation, and real-time host memory telemetry.
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import websockets

from molab_cli.exceptions import MoLabError
from molab_cli.sandbox import SandboxSession


class MarimoBackendError(MoLabError):
    """Raised when native Marimo REST or WebSocket backend returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="MARIMO_BACKEND_ERROR", details=details)
        self.status_code = status_code


class MarimoBackendClient:
    """High-speed native client for the remote Marimo server on cloud pods."""

    def __init__(self, session: SandboxSession):
        self.session = session

    def _ensure_resolved(self) -> None:
        if not self.session.sandbox_id or not self.session.auth_token:
            self.session.resolve()

    @property
    def base_url(self) -> str:
        self._ensure_resolved()
        return self.session.base_url

    @property
    def token(self) -> str:
        self._ensure_resolved()
        return self.session.auth_token

    def _http_client(self, timeout: float = 20.0) -> httpx.Client:
        return httpx.Client(timeout=timeout)

    # =========================================================================
    # System Telemetry & Hardware Usage
    # =========================================================================

    def get_usage(self) -> Dict[str, Any]:
        """Fetch real-time cgroup memory, available host memory, and Marimo process RAM."""
        url = f"{self.base_url}/api/usage?token={self.token}"
        with self._http_client() as client:
            resp = client.get(url)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch usage: {resp.text}", status_code=resp.status_code)
            data = resp.json()

        mem = data.get("memory", {})
        server_mem = data.get("server", {}).get("memory")
        kernel_mem = data.get("kernel", {}).get("memory")

        total_gb = round(mem.get("total", 0) / (1024**3), 2)
        used_gb = round(mem.get("used", 0) / (1024**3), 2)
        free_gb = round(mem.get("available", 0) / (1024**3), 2)
        raw_pct = mem.get("percent", 0.0)
        percent = round(raw_pct, 2) if raw_pct > 1.0 else round(raw_pct * 100, 2)

        server_mb = round(server_mem / (1024**2), 2) if server_mem is not None else 0.0
        kernel_mb = round(kernel_mem / (1024**2), 2) if kernel_mem is not None else 0.0

        cpu_percent = round(data.get("cpu", {}).get("percent", 0.0) * 100, 2)

        gpus = []
        for g in data.get("gpu", []):
            g_mem = g.get("memory", {})
            gpus.append({
                "index": g.get("index", 0),
                "name": g.get("name", "Unknown GPU"),
                "total_gb": round(g_mem.get("total", 0) / (1024**3), 2),
                "used_gb": round(g_mem.get("used", 0) / (1024**3), 2),
                "free_gb": round(g_mem.get("free", 0) / (1024**3), 2),
                "percent_used": round(g_mem.get("percent", 0) * 100, 2),
            })

        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent_used": percent,
            "server_memory_mb": server_mb,
            "kernel_memory_mb": kernel_mb,
            "cpu_percent": cpu_percent,
            "gpus": gpus,
            "raw": data,
        }

    def get_server_version(self) -> str:
        """Fetch remote Marimo version string."""
        url = f"{self.base_url}/api/version?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.text.strip().strip('"')
            return "unknown"

    def get_environment(self) -> Dict[str, Any]:
        """Fetch complete remote system environment specs (OS, gVisor, Python, Node, uv, dependencies)."""
        url = f"{self.base_url}/api/environment?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch environment: {resp.text}", status_code=resp.status_code)
            return resp.json()

    def get_server_status(self) -> Dict[str, Any]:
        """Fetch server health, notebook filenames, active sessions, and LSP state."""
        url = f"{self.base_url}/api/status?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch server status: {resp.text}", status_code=resp.status_code)
            return resp.json()

    def get_connections(self) -> Dict[str, Any]:
        """Fetch active WebSocket connection counts."""
        url = f"{self.base_url}/api/status/connections?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch connections: {resp.text}", status_code=resp.status_code)
            return resp.json()

    def get_sessions(self) -> Dict[str, Any]:
        """Fetch dictionary of active notebook sessions."""
        url = f"{self.base_url}/api/sessions?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch sessions: {resp.text}", status_code=resp.status_code)
            return resp.json()

    def get_thumbnail(self, output_path: Optional[str] = None) -> Union[str, bytes]:
        """Generate and retrieve visual Open Graph SVG thumbnail of the notebook."""
        url = f"{self.base_url}/og/thumbnail?token={self.token}"
        headers = {"Authorization": f"Bearer {self.token}"}
        with self._http_client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to generate thumbnail: {resp.text}", status_code=resp.status_code)
            content = resp.content

        if output_path:
            with open(output_path, "wb") as f:
                f.write(content)
            return output_path

        try:
            return content.decode("utf-8")
        except Exception:
            return content

    # =========================================================================
    # Native Server-Side File Operations
    # =========================================================================

    def list_files(self, path: str = ".") -> List[Dict[str, Any]]:
        """List files and directories directly over native HTTP JSON endpoint."""
        url = f"{self.base_url}/api/files/list_files?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": path})
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to list files: {resp.text}", status_code=resp.status_code)
            data = resp.json()
            return data.get("files", [])

    def file_details(self, path: str) -> Dict[str, Any]:
        """Fetch metadata, mime type, and contents of a file."""
        url = f"{self.base_url}/api/files/file_details?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": path})
            if resp.status_code != 200:
                raise MarimoBackendError(f"Failed to fetch file details: {resp.text}", status_code=resp.status_code)
            return resp.json()

    def read_file(self, path: str) -> str:
        """Read text contents of a remote file via Marimo file details API."""
        details = self.file_details(path)
        contents = details.get("contents")
        if contents is None:
            raise MarimoBackendError(f"Could not read content for path: {path}")
        return contents

    def update_file(self, path: str, contents: str) -> bool:
        """Update contents of an existing file on the remote pod."""
        url = f"{self.base_url}/api/files/update?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": path, "contents": contents})
            if resp.status_code == 200:
                data = resp.json()
                return data.get("success", False)
            return False

    def copy_file(self, src: str, dst: str) -> bool:
        """Instant server-side copy without transferring data back to client."""
        url = f"{self.base_url}/api/files/copy?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": src, "newPath": dst})
            return resp.status_code == 200

    def move_file(self, src: str, dst: str) -> bool:
        """Instant server-side move or rename."""
        url = f"{self.base_url}/api/files/move?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": src, "newPath": dst})
            return resp.status_code == 200

    def delete_file(self, path: str) -> bool:
        """Delete file or directory directly on the pod."""
        url = f"{self.base_url}/api/files/delete?token={self.token}"
        with self._http_client() as client:
            resp = client.post(url, json={"path": path})
            return resp.status_code == 200

    def search_files(
        self,
        query: str,
        path: Optional[str] = None,
        depth: int = 5,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fast server-side file and directory search."""
        url = f"{self.base_url}/api/files/search?token={self.token}"
        payload = {
            "query": query,
            "path": path,
            "depth": depth,
            "limit": limit,
            "includeDirectories": True,
            "includeFiles": True,
            "includeHidden": False,
        }
        with self._http_client() as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json().get("files", [])
            return []

    # =========================================================================
    # Notebook Export & Conversion
    # =========================================================================

    def export_notebook(
        self,
        format_type: str = "html",
        file_key: str = "notebook.py",
        include_code: bool = True,
    ) -> Union[str, bytes]:
        """
        Export reactive Marimo notebook directly to HTML, Markdown, IPYNB, Script, or PDF.
        Bypasses local dependencies by leveraging the remote Marimo server exporter.
        """
        fmt = format_type.lower()
        if fmt in ("md", "markdown"):
            endpoint = "markdown"
            payload: Dict[str, Any] = {"download": False}
        elif fmt in ("ipynb", "jupyter"):
            endpoint = "ipynb"
            payload = {"download": False}
        elif fmt in ("script", "py", "python"):
            endpoint = "script"
            payload = {"download": False}
        elif fmt == "pdf":
            endpoint = "pdf"
            payload = {"download": False}
        elif fmt in ("html", "htm"):
            endpoint = "html"
            payload = {
                "download": False,
                "files": [file_key],
                "includeCode": include_code,
            }
        else:
            raise MarimoBackendError(
                f"Unsupported export format: {format_type}. Supported: html, markdown, ipynb, script, pdf"
            )

        sess_id = f"molab_export_{uuid.uuid4().hex[:8]}"
        ws_url = (
            self.base_url.replace("https://", "wss://").replace("http://", "ws://")
            + f"/ws?session_id={sess_id}&file={file_key}&token={self.token}"
        )

        async def _run_export():
            # Open temporary session channel
            async with websockets.connect(ws_url, close_timeout=3.0):
                headers = {"Marimo-Session-Id": sess_id}
                url = f"{self.base_url}/api/export/{endpoint}?token={self.token}"
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code != 200:
                        raise MarimoBackendError(
                            f"Export to {format_type} failed ({resp.status_code}): {resp.text}",
                            status_code=resp.status_code,
                        )
                    if fmt == "pdf":
                        return resp.content
                    return resp.text

        return asyncio.run(_run_export())

    # =========================================================================
    # Native Kernel Execution & Evaluation
    # =========================================================================

    def eval_python(
        self,
        code: str,
        file_key: str = "notebook.py",
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Execute arbitrary Python code directly in the Marimo Python kernel without terminal PTY.
        Streams execution output and captures stdout, stderr, and MIME outputs.
        """
        sess_id = f"molab_eval_{uuid.uuid4().hex[:8]}"
        ws_url = (
            self.base_url.replace("https://", "wss://").replace("http://", "ws://")
            + f"/ws?session_id={sess_id}&file={file_key}&token={self.token}"
        )

        async def _run_eval():
            async with websockets.connect(ws_url, close_timeout=5.0) as ws:
                # 1. Wait for kernel to be ready
                while True:
                    m = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    msg_data = json.loads(m)
                    if msg_data.get("op") == "kernel-ready":
                        break

                # 2. Drain any initial handshake messages to prevent stale output leaking
                try:
                    while True:
                        await asyncio.wait_for(ws.recv(), timeout=0.2)
                except asyncio.TimeoutError:
                    pass

                # 3. Dispatch scratchpad execution request
                headers = {"Marimo-Session-Id": sess_id}
                url = f"{self.base_url}/api/kernel/scratchpad/run?token={self.token}"
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, headers=headers, json={"code": code})
                    if resp.status_code != 200:
                        raise MarimoBackendError(f"Kernel eval request failed: {resp.text}", status_code=resp.status_code)

                # 4. Collect output messages from WebSocket
                stdout_chunks: List[str] = []
                stderr_chunks: List[str] = []
                final_output = None
                start_t = time.time()

                def _append_console(item: Any):
                    if not item:
                        return
                    if isinstance(item, dict):
                        text = item.get("data", "")
                        if item.get("channel") == "stderr":
                            stderr_chunks.append(text)
                        else:
                            stdout_chunks.append(text)
                    elif isinstance(item, list):
                        for sub in item:
                            _append_console(sub)
                    elif isinstance(item, str):
                        stdout_chunks.append(item)

                while (time.time() - start_t) < timeout:
                    try:
                        m = await asyncio.wait_for(ws.recv(), timeout=3.0)
                        d = json.loads(m)
                        if d.get("op") == "cell-op":
                            cell_data = d.get("data", {})
                            if cell_data.get("cell_id") == "__scratch__":
                                console = cell_data.get("console")
                                _append_console(console)

                                output = cell_data.get("output")
                                if output:
                                    final_output = output

                                if cell_data.get("status") == "idle":
                                    break
                    except asyncio.TimeoutError:
                        break

                clean_output = None
                if final_output and isinstance(final_output, dict):
                    raw_data = final_output.get("data", "")
                    if isinstance(raw_data, str):
                        import re
                        clean_output = re.sub(r"<[^>]+>", "", raw_data).strip()
                    else:
                        clean_output = str(raw_data)

                return {
                    "success": True,
                    "stdout": "".join(stdout_chunks),
                    "stderr": "".join(stderr_chunks),
                    "output": final_output,
                    "output_text": clean_output,
                }

        return asyncio.run(_run_eval())

    def get_kernel_status(self, file_key: str = "notebook.py") -> Dict[str, Any]:
        """Check if Python kernel is IDLE or RUNNING."""
        sess_id = f"molab_kstatus_{uuid.uuid4().hex[:8]}"
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?session_id={sess_id}&file={file_key}&token={self.token}"

        async def _check_status():
            async with websockets.connect(ws_url, close_timeout=3.0):
                headers = {"Marimo-Session-Id": sess_id}
                url = f"{self.base_url}/api/kernel/status?token={self.token}"
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                    return {"state": "unknown"}

        return asyncio.run(_check_status())

    def restart_kernel(self, file_key: str = "notebook.py") -> bool:
        """Perform a soft restart of the Python kernel without restarting the pod container."""
        sess_id = f"molab_krestart_{uuid.uuid4().hex[:8]}"
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?session_id={sess_id}&file={file_key}&token={self.token}"

        async def _restart():
            async with websockets.connect(ws_url, close_timeout=3.0):
                headers = {"Marimo-Session-Id": sess_id}
                url = f"{self.base_url}/api/kernel/restart_session?token={self.token}"
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, headers=headers, json={})
                    return resp.status_code == 200

        return asyncio.run(_restart())

    def interrupt_kernel(self, file_key: str = "notebook.py") -> bool:
        """Interrupt any long-running execution cell in the kernel."""
        sess_id = f"molab_kint_{uuid.uuid4().hex[:8]}"
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?session_id={sess_id}&file={file_key}&token={self.token}"

        async def _interrupt():
            async with websockets.connect(ws_url, close_timeout=3.0):
                headers = {"Marimo-Session-Id": sess_id}
                url = f"{self.base_url}/api/kernel/interrupt?token={self.token}"
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(url, headers=headers, json={})
                    return resp.status_code == 200

        return asyncio.run(_interrupt())

    # =========================================================================
    # Native Package Management
    # =========================================================================

    def list_packages(self) -> List[Dict[str, Any]]:
        """List packages installed on the pod."""
        url = f"{self.base_url}/api/packages/list?token={self.token}"
        with self._http_client() as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.json().get("packages", [])
            return []

    def add_package(self, package_name: str, upgrade: bool = False) -> Dict[str, Any]:
        """Install a Python package natively on the pod."""
        url = f"{self.base_url}/api/packages/add?token={self.token}"
        with self._http_client(timeout=120.0) as client:
            resp = client.post(url, json={"package": package_name, "upgrade": upgrade})
            if resp.status_code == 200:
                return resp.json()
            raise MarimoBackendError(f"Package installation failed: {resp.text}", status_code=resp.status_code)
