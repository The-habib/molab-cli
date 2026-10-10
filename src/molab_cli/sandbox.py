"""
CoreWeave Sandbox connection, interactive terminal, and remote execution engine.
"""

import asyncio
import base64
import http.server
import json
import os
import re
import select
import socketserver
import subprocess
import sys
import termios
import time
import tty
import urllib.request
import uuid
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import websockets

from molab_cli.client import MoLabClient


class SandboxSession:
    """Manages connection to an ephemeral CoreWeave sandbox pod."""

    def __init__(self, notebook_id: str, client: Optional[MoLabClient] = None):
        self.notebook_id = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
        self.client = client or MoLabClient()
        self.sandbox_id: Optional[str] = None
        self.auth_token: Optional[str] = None
        self.expires_at: Optional[int] = None
        self._resolved = False

    def resolve(self, force_refresh: bool = False) -> None:
        """Resolve sandbox host and token from live notebook page."""
        if self._resolved and not force_refresh:
            return

        info = self.client.inspect_notebook(self.notebook_id)
        session_b64 = info.get("session_token_b64")
        if not session_b64:
            raise RuntimeError(f"Could not locate active sandbox session for {self.notebook_id}")

        token_obj = None
        try:
            padded = session_b64 + "=" * ((4 - len(session_b64) % 4) % 4)
            raw_text = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
            token_obj, _ = json.JSONDecoder().raw_decode(raw_text)
        except Exception:
            pass

        if not token_obj or not token_obj.get("auth_token"):
            try:
                token_obj, _ = json.JSONDecoder().raw_decode(session_b64)
            except Exception:
                pass

        if not token_obj or not token_obj.get("auth_token"):
            sb_m = re.search(r'"sandbox_id":"([^"]+)"', session_b64)
            tok_m = re.search(r'"auth_token":"([^"]+)"', session_b64)
            exp_m = re.search(r'"expires_at":(\d+)', session_b64)
            token_obj = {
                "sandbox_id": sb_m.group(1) if sb_m else None,
                "auth_token": tok_m.group(1) if tok_m else None,
                "expires_at": int(exp_m.group(1)) if exp_m else None,
            }

        self.sandbox_id = token_obj.get("sandbox_id")
        self.auth_token = token_obj.get("auth_token")
        self.expires_at = token_obj.get("expires_at")
        self._resolved = True

    @property
    def base_url(self) -> str:
        self.resolve()
        return f"https://{self.sandbox_id}.sb.molab.run"

    @property
    def ws_terminal_url(self) -> str:
        self.resolve()
        rows, cols = self._get_terminal_size()
        return f"wss://{self.sandbox_id}.sb.molab.run/terminal/ws?token={self.auth_token}&rows={rows}&cols={cols}"

    def check_health(self) -> Dict[str, Any]:
        """Check health status of the sandbox via HTTP."""
        self.resolve()
        url = f"{self.base_url}/api/status?token={self.auth_token}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_notebook_cells(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetch cell AST and mount configuration from the sandbox."""
        self.resolve()
        # Fetch the sandbox preview/edit page
        session_b64 = self.client.inspect_notebook(self.notebook_id).get("session_token_b64")
        full_sb_url = f"https://{self.sandbox_id}-session.sb.molab.run/session/{session_b64}"
        status, html, _ = self.client.fetch_url(full_sb_url)
        if status != 200:
            raise RuntimeError(f"Failed to fetch sandbox mount page (HTTP {status})")

        idx = html.find('"notebook":')
        if idx != -1:
            idx2 = html.find('"cells":', idx)
            if idx2 != -1:
                start_bracket = html.find('[', idx2)
                if start_bracket != -1:
                    depth = 0
                    end_bracket = -1
                    for i in range(start_bracket, len(html)):
                        if html[i] == '[':
                            depth += 1
                        elif html[i] == ']':
                            depth -= 1
                            if depth == 0:
                                end_bracket = i + 1
                                break
                    if end_bracket != -1:
                        try:
                            cells = json.loads(html[start_bracket:end_bracket])
                            v_match = re.search(r'"version":\s*"([^"]+)"', html)
                            f_match = re.search(r'"filename":\s*"([^"]+)"', html)
                            m_match = re.search(r'"mode":\s*"([^"]+)"', html)
                            cfg = {
                                "filename": f_match.group(1) if f_match else "notebook.py",
                                "version": v_match.group(1) if v_match else "0.25.1",
                                "mode": m_match.group(1) if m_match else "edit",
                            }
                            return cfg, cells
                        except Exception:
                            pass
        return {}, []

    def execute_command(self, cmd: str, timeout: float = 25.0) -> str:
        """
        Execute a one-shot bash command inside the sandbox pod and return stdout/stderr.
        """
        self.resolve()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self._async_execute(cmd, timeout))
                return future.result()
        else:
            return asyncio.run(self._async_execute(cmd, timeout))

    async def _async_execute(self, cmd: str, timeout: float) -> str:
        uri = self.ws_terminal_url
        async with websockets.connect(uri) as ws:
            # Drain initial prompt / welcome text
            await asyncio.sleep(0.3)
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                pass

            start_token = f"___START_{uuid.uuid4().hex[:6]}___"
            s1, s2 = start_token[:8], start_token[8:]
            end_token = f"___END_{uuid.uuid4().hex[:6]}___"
            e1, e2 = end_token[:6], end_token[6:]

            wrapped_cmd = f"printf \"%s%s\\n\" \"{s1}\" \"{s2}\" ; {cmd.strip()} ; printf \"\\n%s%s:%s\\n\" \"{e1}\" \"{e2}\" \"$?\"\n"
            await ws.send(wrapped_cmd)

            out_chunks = []
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = max(0.5, deadline - time.time())
                try:
                    chunk = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 4.0))
                    out_chunks.append(chunk)
                    full = "".join(out_chunks)
                    if end_token in full:
                        break
                except asyncio.TimeoutError:
                    if time.time() >= deadline:
                        break

            full_output = "".join(out_chunks)
            clean = re.sub(r"\x1b\][^\x07\x1b]*\x07|\x1b\[[0-9;?]*[a-zA-Z]", "", full_output).replace("\r", "")

            # Output between start_token and end_token is the exact command output
            s_idx = clean.find(start_token)
            e_idx = clean.find(end_token)
            if s_idx != -1 and e_idx != -1:
                return clean[s_idx + len(start_token):e_idx].strip()
            elif e_idx != -1:
                return clean[:e_idx].strip()
            return clean.strip()

    def interactive_shell(self) -> None:
        """
        Open a raw, bidirectional interactive root bash terminal inside the CoreWeave container.
        """
        self.resolve()
        asyncio.run(self._async_interactive_shell())

    async def _async_interactive_shell(self) -> None:
        uri = self.ws_terminal_url
        async with websockets.connect(uri) as ws:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)

                async def ws_to_stdout():
                    try:
                        while True:
                            msg = await ws.recv()
                            if isinstance(msg, bytes):
                                sys.stdout.buffer.write(msg)
                                sys.stdout.buffer.flush()
                            else:
                                sys.stdout.write(msg)
                                sys.stdout.flush()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass

                async def stdin_to_ws():
                    loop = asyncio.get_event_loop()
                    try:
                        while True:
                            # Read byte from stdin asynchronously
                            char = await loop.run_in_executor(None, os.read, fd, 1024)
                            if not char:
                                break
                            await ws.send(char.decode("utf-8", errors="ignore"))
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass

                t1 = asyncio.create_task(ws_to_stdout())
                t2 = asyncio.create_task(stdin_to_ws())

                done, pending = await asyncio.wait(
                    [t1, t2],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                print("\n[Disconnected from MoLab Cloud Pod]")

    def push_file(self, local_path: str, remote_path: Optional[str] = None, recursive: bool = False) -> Tuple[str, int]:
        """
        Transfer a local file or directory directly into the CoreWeave sandbox container using the native Marimo HTTP upload API.
        """
        p = os.path.abspath(os.path.expanduser(local_path))
        if not os.path.exists(p):
            raise FileNotFoundError(f"Local file does not exist: {local_path}")

        self.resolve()

        # Handle directory push via tarball streaming
        if os.path.isdir(p) or recursive:
            filename = os.path.basename(p.rstrip("/"))
            dest_dir = remote_path or f"/workspace/{filename}"
            self.execute_command(f"mkdir -p {dest_dir}")

            scratch_dir = "/data/data/com.termux/files/home/.gemini/antigravity-cli/brain"
            os.makedirs(scratch_dir, exist_ok=True)
            tar_name = f"_up_{uuid.uuid4().hex[:8]}.tar.gz"
            temp_tar = os.path.join(scratch_dir, tar_name)
            try:
                subprocess.run(
                    ["tar", "-czf", temp_tar, "-C", os.path.dirname(p), filename],
                    check=True, capture_output=True
                )
                tar_size = os.path.getsize(temp_tar)

                # Upload tarball to pod /tmp
                upload_url = f"{self.base_url}/api/files/create?token={self.auth_token}"
                curl_cmd = [
                    "curl", "-s", "-f", "-X", "POST",
                    upload_url,
                    "-F", "path=/tmp",
                    "-F", "type=file",
                    "-F", f"name={tar_name}",
                    "-F", f"file=@{temp_tar}"
                ]
                res = subprocess.run(curl_cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"HTTP upload of directory tarball failed: {res.stderr}")

                # Extract on pod and cleanup remote tarball
                self.execute_command(f"tar -xzf /tmp/{tar_name} -C $(dirname {dest_dir}) && rm -f /tmp/{tar_name}")
                return dest_dir, tar_size
            finally:
                if os.path.exists(temp_tar):
                    os.remove(temp_tar)

        # Handle single file push
        filename = os.path.basename(p)
        if remote_path and (remote_path.endswith("/") or self.execute_command(f"[ -d {remote_path} ] && echo 1 || echo 0").strip() == "1"):
            dest_dir = remote_path.rstrip("/")
            dest_name = filename
            dest = f"{dest_dir}/{dest_name}"
        else:
            dest = remote_path or f"/workspace/{filename}"
            dest_dir = os.path.dirname(dest) or "/workspace"
            dest_name = os.path.basename(dest)

        file_size = os.path.getsize(p)

        # Ensure parent destination directory exists on pod
        self.execute_command(f"mkdir -p {dest_dir}")

        # Stream directly via native Marimo HTTP multipart file upload API
        upload_url = f"{self.base_url}/api/files/create?token={self.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-X", "POST",
            upload_url,
            "-F", f"path={dest_dir}",
            "-F", "type=file",
            "-F", f"name={dest_name}",
            "-F", f"file=@{p}"
        ]
        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"HTTP upload failed (code {res.returncode}): {res.stderr}")

        return dest, file_size

    def pull_file(self, remote_path: str, local_path: Optional[str] = None, recursive: bool = False) -> Tuple[str, int]:
        """
        Download a file or directory from the CoreWeave sandbox container using the native Marimo HTTP download API.
        """
        self.resolve()
        dest = local_path or os.path.basename(remote_path.rstrip("/"))
        dest_p = os.path.abspath(os.path.expanduser(dest))

        # Check if remote path is a directory
        is_remote_dir = recursive or self.execute_command(f"[ -d {remote_path} ] && echo 1 || echo 0").strip() == "1"
        if is_remote_dir:
            scratch_dir = "/data/data/com.termux/files/home/.gemini/antigravity-cli/brain"
            os.makedirs(scratch_dir, exist_ok=True)
            tar_name = f"_dl_{uuid.uuid4().hex[:8]}.tar.gz"
            parent_dir = self.execute_command(f"dirname {remote_path}").strip()
            base_name = self.execute_command(f"basename {remote_path}").strip()
            self.execute_command(f"tar -czf /tmp/{tar_name} -C {parent_dir} {base_name}")

            temp_local_tar = os.path.join(scratch_dir, tar_name)
            try:
                download_url = f"{self.base_url}/api/files/download?path=/tmp/{tar_name}&token={self.auth_token}"
                subprocess.run(["curl", "-s", "-f", "-L", "-o", temp_local_tar, download_url], check=True)

                os.makedirs(dest_p, exist_ok=True)
                extract_target = os.path.dirname(dest_p) if not os.path.isdir(dest_p) else dest_p
                subprocess.run(["tar", "-xzf", temp_local_tar, "-C", extract_target], check=True)
                size = os.path.getsize(temp_local_tar)
                self.execute_command(f"rm -f /tmp/{tar_name}")
                return dest_p, size
            finally:
                if os.path.exists(temp_local_tar):
                    os.remove(temp_local_tar)

        if os.path.isdir(dest_p):
            dest_p = os.path.join(dest_p, os.path.basename(remote_path))

        os.makedirs(os.path.dirname(dest_p), exist_ok=True)

        download_url = f"{self.base_url}/api/files/download?path={remote_path}&token={self.auth_token}"
        curl_cmd = [
            "curl", "-s", "-f", "-L",
            "-o", dest_p,
            download_url
        ]
        res = subprocess.run(curl_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"HTTP download failed (code {res.returncode}): {res.stderr}")

        return dest_p, os.path.getsize(dest_p)

    def get_workload_status(self) -> Dict[str, Any]:
        """
        Query real-time workload status, VRAM allocation, and detect running background services.
        Useful for distinguishing free/idle pods from occupied pods.
        """
        telemetry = self.get_gpu_telemetry()
        ps_out = self.execute_command("ps aux --sort=-%mem | grep -v 'marimo\\|grep\\|ps aux' | head -n 15")

        has_server = False
        active_workloads = []
        for line in ps_out.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 11:
                cmd = " ".join(parts[10:])
                if any(x in cmd for x in ["server.py", "vllm", "uvicorn", "train", "python -m"]):
                    has_server = True
                    active_workloads.append({
                        "user": parts[0],
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "command": cmd[:80]
                    })

        allocated_vram = telemetry.get("allocated_vram_gb", 0)
        total_vram = telemetry.get("total_vram_gb", 96.0)
        free_vram = telemetry.get("free_vram_gb", total_vram - allocated_vram)
        is_occupied = has_server or (allocated_vram > 20.0)

        return {
            "notebook_id": self.notebook_id,
            "sandbox_id": self.sandbox_id,
            "cuda_available": telemetry.get("cuda_available", False),
            "device_name": telemetry.get("device_name", "N/A"),
            "total_vram_gb": total_vram,
            "allocated_vram_gb": allocated_vram,
            "free_vram_gb": free_vram,
            "is_occupied": is_occupied,
            "status": "OCCUPIED" if is_occupied else "FREE / IDLE",
            "active_workloads": active_workloads,
        }

    def get_gpu_telemetry(self) -> Dict[str, Any]:
        """
        Query NVIDIA Blackwell GPU device properties, memory allocation, and CUDA specs.
        """
        py_code = (
            "import torch, json, os\n"
            "avail = torch.cuda.is_available()\n"
            "data = {'cuda_available': avail}\n"
            "try:\n"
            "    data['host_ram_gb'] = round(int(open('/proc/meminfo').readline().split()[1]) / (1024**2), 1)\n"
            "except Exception: pass\n"
            "data['cpu_cores'] = os.cpu_count()\n"
            "if avail:\n"
            "    p = torch.cuda.get_device_properties(0)\n"
            "    free_b, total_b = torch.cuda.mem_get_info(0)\n"
            "    used_b = total_b - free_b\n"
            "    data.update({\n"
            "        'device_name': torch.cuda.get_device_name(0),\n"
            "        'total_vram_gb': round(total_b / (1024**3), 2),\n"
            "        'allocated_vram_gb': round(used_b / (1024**3), 2),\n"
            "        'free_vram_gb': round(free_b / (1024**3), 2),\n"
            "        'sm_count': p.multi_processor_count,\n"
            "        'compute_capability': f'{p.major}.{p.minor}',\n"
            "        'cuda_version': torch.version.cuda,\n"
            "        'torch_version': torch.__version__,\n"
            "    })\n"
            "print('___TELEMETRY___' + json.dumps(data))\n"
        )
        b64 = base64.b64encode(py_code.encode("utf-8")).decode("utf-8")
        out = self.execute_command(f"echo {b64} | base64 -d | python3")
        idx = out.find("___TELEMETRY___")
        if idx != -1:
            raw_json = out[idx + len("___TELEMETRY___"):].strip().split("\n")[0]
            try:
                return json.loads(raw_json)
            except Exception:
                pass
        return {"cuda_available": False, "raw_output": out}

    def install_packages(self, packages: List[str]) -> str:
        """
        Install Python packages inside the remote container using uv/pip.
        """
        pkgs_str = " ".join(packages)
        cmd = f"uv pip install {pkgs_str} 2>/dev/null || pip install {pkgs_str}"
        return self.execute_command(cmd, timeout=90.0)

    def _get_terminal_size(self) -> Tuple[int, int]:
        try:
            sz = os.get_terminal_size()
            return sz.lines, sz.columns
        except Exception:
            return 30, 100

    def get_active_model(self) -> Optional[str]:
        """Fetch the currently active model ID from the pod's vLLM / OpenAI server."""
        raw = self.execute_command("curl -s http://127.0.0.1:8000/v1/models", timeout=6.0)
        try:
            idx = raw.find("{")
            if idx != -1:
                data = json.loads(raw[idx:])
                models = data.get("data", [])
                if models and "id" in models[0]:
                    return models[0]["id"]
        except Exception:
            pass
        return None

    async def async_get_active_model(self) -> Optional[str]:
        """Async fetch the currently active model ID from the pod's vLLM / OpenAI server."""
        raw = await self._async_execute("curl -s http://127.0.0.1:8000/v1/models", timeout=6.0)
        try:
            idx = raw.find("{")
            if idx != -1:
                data = json.loads(raw[idx:])
                models = data.get("data", [])
                if models and "id" in models[0]:
                    return models[0]["id"]
        except Exception:
            pass
        return None


    @classmethod
    def discover_active_pod(cls, client: Optional[Any] = None) -> Optional[str]:
        """Auto-discover the running or free GPU pod in the user's workspace."""
        from molab_cli.client import MoLabClient
        c = client or MoLabClient()
        try:
            free_info = c.get_free_pod()
            if free_info and free_info.get("recommended_free_pod"):
                return free_info["recommended_free_pod"]
        except Exception:
            pass

        try:
            running = c.list_running_sandboxes()
            if running:
                return running[0].get("notebook_id")
        except Exception:
            pass

        try:
            nbs = c.list_notebooks()
            for nb in nbs:
                if nb.get("is_running") or nb.get("sandbox_id"):
                    return nb.get("id")
            if nbs:
                return nbs[0].get("id")
        except Exception:
            pass
        return None

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query the OpenAI-compatible model server running inside the cloud pod.
        Auto-detects active model if not specified.
        """
        if not model:
            model = self.get_active_model() or "default"

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        b64_body = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
        cmd = f"echo '{b64_body}' | base64 -d | curl -s -X POST http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' --data-binary @-"
        raw = self.execute_command(cmd, timeout=120.0)
        idx = raw.find("{")
        if idx != -1:
            try:
                return json.loads(raw[idx:])
            except Exception:
                pass
        return {"choices": [{"message": {"content": raw}}]}
    async def _upload_temp_json_payload(self, json_bytes: bytes) -> str:
        """Upload a JSON request payload to pod /tmp using native Marimo multipart upload."""
        req_name = f"_req_{uuid.uuid4().hex[:8]}.json"
        upload_url = f"{self.base_url}/api/files/create?token={self.auth_token}"
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(json_bytes)
            tf_path = tf.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-f", "-X", "POST", upload_url,
                "-F", "path=/tmp",
                "-F", "type=file",
                "-F", f"name={req_name}",
                "-F", f"file=@{tf_path}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        finally:
            if os.path.exists(tf_path):
                try:
                    os.unlink(tf_path)
                except OSError:
                    pass
        return req_name

    async def async_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.7,
        model: Optional[str] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Async query the OpenAI-compatible model server running inside the cloud pod."""
        if not model:
            model = await self.async_get_active_model() or "default"

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if extra_payload:
            for k, v in extra_payload.items():
                if k not in payload:
                    payload[k] = v

        self.resolve()
        json_bytes = json.dumps(payload).encode("utf-8")
        if len(json_bytes) > 1500:
            req_name = await self._upload_temp_json_payload(json_bytes)
            cmd = f"curl -s -X POST http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' --data-binary @/tmp/{req_name} ; rm -f /tmp/{req_name}"
        else:
            b64_body = base64.b64encode(json_bytes).decode("utf-8")
            cmd = f"echo '{b64_body}' | base64 -d | curl -s -X POST http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' --data-binary @-"

        raw = await self._async_execute(cmd, timeout=120.0)
        idx = raw.find("{")
        if idx != -1:
            try:
                return json.loads(raw[idx:])
            except Exception:
                pass
        return {"choices": [{"message": {"content": raw}}]}

    async def async_stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        model: Optional[str] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ):
        """Async generator streaming tokens and reasoning deltas directly from the pod."""
        if not model:
            model = await self.async_get_active_model() or "default"

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if extra_payload:
            for k, v in extra_payload.items():
                if k not in payload:
                    payload[k] = v

        self.resolve()
        json_bytes = json.dumps(payload).encode("utf-8")
        if len(json_bytes) > 1500:
            req_name = await self._upload_temp_json_payload(json_bytes)
            cmd = f"curl -N -s -X POST http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' --data-binary @/tmp/{req_name} ; rm -f /tmp/{req_name}"
        else:
            b64_body = base64.b64encode(json_bytes).decode("utf-8")
            cmd = f"echo '{b64_body}' | base64 -d | curl -N -s -X POST http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' --data-binary @-"

        self.resolve()
        uri = self.ws_terminal_url

        async with websockets.connect(uri) as ws:
            # Drain initial prompt / welcome text
            await asyncio.sleep(0.3)
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                pass

            await ws.send(f"{cmd}\n")
            line_buffer = ""
            while True:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=20.0)
                    clean = re.sub(r"\x1b\][^\x07\x1b]*\x07|\x1b\[[0-9;?]*[a-zA-Z]", "", frame)
                    line_buffer += clean
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data: "):
                            chunk_str = line[6:].strip()
                            if chunk_str == "[DONE]":
                                return
                            try:
                                data = json.loads(chunk_str)
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    reasoning = delta.get("reasoning_content", "")
                                    finish_reason = choices[0].get("finish_reason")
                                    tool_calls = delta.get("tool_calls")
                                    if content or reasoning or finish_reason or tool_calls:
                                        yield {
                                            "content": content,
                                            "reasoning_content": reasoning,
                                            "finish_reason": finish_reason,
                                            "tool_calls": tool_calls,
                                            "raw": data,
                                        }
                            except Exception:
                                pass
                        elif line.startswith("{") and '"error"' in line:
                            try:
                                err_data = json.loads(line)
                                if "error" in err_data:
                                    msg = err_data["error"].get("message", line)
                                    raise RuntimeError(f"vLLM server error: {msg}")
                            except json.JSONDecodeError:
                                pass
                except asyncio.TimeoutError:
                    break

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        model: Optional[str] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ):
        """Synchronous wrapper for async_stream_chat_completion."""
        import queue
        import threading

        q = queue.Queue()

        def _worker():
            async def _run():
                try:
                    async for delta in self.async_stream_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        model=model,
                        extra_payload=extra_payload,
                    ):
                        q.put(delta)
                except Exception as ex:
                    q.put(ex)
                finally:
                    q.put(StopIteration)

            asyncio.run(_run())

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item is StopIteration:
                break
            elif isinstance(item, Exception):
                raise item
            yield item

    def ensure_model_server_running(self) -> bool:
        """
        Check if the OpenAI-compatible model server is running on the pod;
        if stopped, launch it and wait for it to become healthy.
        """
        status_code = self.execute_command(
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null", timeout=6.0
        ).strip()
        if status_code in ("200", "OK") or "healthy" in status_code:
            return True

        if self.get_active_model():
            return True

        self.execute_command(
            "nohup python3 /workspace/run_qwen.py > /workspace/qwen_server.log 2>&1 & "
            "|| bash /workspace/start.sh 2>/dev/null || bash /marimo/start_server.sh 2>/dev/null",
            timeout=15.0,
        )
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(3.0)
            status_code = self.execute_command(
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null", timeout=6.0
            ).strip()
            if status_code in ("200", "OK") or self.get_active_model():
                return True
        return False



class LocalHttpForwarder:
    """
    Lightweight local HTTP proxy that forwards localhost calls directly to the
    remote model server running inside the CoreWeave container pod.
    """

    def __init__(self, session: SandboxSession, port: int = 8000):
        self.session = session
        self.port = port

    def start(self):
        session = self.session

        class ProxyHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                auth_hdr = f"-H 'Authorization: {auth}' " if auth else ""
                cmd = f"curl -s -X GET {auth_hdr}http://127.0.0.1:8000{self.path}"
                raw = session.execute_command(cmd, timeout=30.0)
                self._send_response(raw)

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else b""
                auth = self.headers.get("Authorization", "")
                auth_hdr = f"-H 'Authorization: {auth}' " if auth else ""

                if len(body) > 3000:
                    req_name = f"_req_{uuid.uuid4().hex[:8]}.json"
                    upload_url = f"{session.base_url}/api/files/create?token={session.auth_token}"
                    subprocess.run(
                        ["curl", "-s", "-f", "-X", "POST", upload_url,
                         "-F", "path=/tmp", "-F", "type=file", "-F", f"name={req_name}",
                         "-F", "file=@-"],
                        input=body, check=True
                    )
                    cmd = f"curl -s -X POST http://127.0.0.1:8000{self.path} {auth_hdr}-H 'Content-Type: application/json' --data-binary @/tmp/{req_name} ; rm -f /tmp/{req_name}"
                    raw = session.execute_command(cmd, timeout=180.0)
                else:
                    b64_body = base64.b64encode(body).decode("utf-8")
                    cmd = f"echo '{b64_body}' | base64 -d | curl -s -X POST http://127.0.0.1:8000{self.path} {auth_hdr}-H 'Content-Type: application/json' --data-binary @-"
                    raw = session.execute_command(cmd, timeout=120.0)

                self._send_response(raw)

            protocol_version = "HTTP/1.1"

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _send_response(self, content_str: str, status_code: int = 200):
                body_bytes = content_str.encode("utf-8")
                try:
                    self.send_response(status_code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.end_headers()
                    self.wfile.write(body_bytes)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        # Allow port reuse and handle concurrent requests via threads
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        with socketserver.ThreadingTCPServer(("127.0.0.1", self.port), ProxyHandler) as httpd:
            httpd.serve_forever()
