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
import sys
import termios
import time
import tty
import urllib.request
import uuid
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

    def push_file(self, local_path: str, remote_path: Optional[str] = None) -> Tuple[str, int]:
        """
        Transfer a local file directly into the CoreWeave sandbox container.
        """
        p = os.path.expanduser(local_path)
        if not os.path.exists(p):
            raise FileNotFoundError(f"Local file does not exist: {local_path}")

        filename = os.path.basename(p)
        dest = remote_path or f"/marimo/{filename}"
        with open(p, "rb") as f:
            data = f.read()

        b64 = base64.b64encode(data).decode("utf-8")
        # Ensure parent directory exists and decode into file
        cmd = f"mkdir -p $(dirname {dest}) && echo '{b64}' | base64 -d > {dest} && ls -la {dest}"
        out = self.execute_command(cmd)
        return dest, len(data)

    def pull_file(self, remote_path: str, local_path: Optional[str] = None) -> Tuple[str, int]:
        """
        Download a file from the CoreWeave sandbox container to local storage.
        """
        dest = local_path or os.path.basename(remote_path)
        dest_p = os.path.abspath(os.path.expanduser(dest))

        # Check file exists and dump base64
        out = self.execute_command(f"base64 {remote_path}")
        clean_b64 = out.strip().replace("\n", "").replace("\r", "")
        try:
            raw_bytes = base64.b64decode(clean_b64)
        except Exception as e:
            raise RuntimeError(f"Failed to decode remote file content: {e}")

        with open(dest_p, "wb") as f:
            f.write(raw_bytes)

        return dest_p, len(raw_bytes)

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

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
        model: str = "gemma-3-27b-it-abliterated",
    ) -> Dict[str, Any]:
        """
        Query the OpenAI-compatible model server running inside the cloud pod.
        """
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

    def ensure_model_server_running(self) -> bool:
        """
        Check if the OpenAI-compatible model server is running on the pod;
        if stopped, launch it and wait for it to become healthy.
        """
        raw = self.execute_command("curl -s http://127.0.0.1:8000/health", timeout=6.0)
        if "healthy" in raw:
            return True

        self.execute_command("bash /marimo/start_server.sh", timeout=10.0)
        deadline = time.time() + 35
        while time.time() < deadline:
            time.sleep(2.0)
            raw = self.execute_command("curl -s http://127.0.0.1:8000/health", timeout=6.0)
            if "healthy" in raw:
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
                cmd = f"curl -s -X GET http://127.0.0.1:8000{self.path}"
                raw = session.execute_command(cmd, timeout=30.0)
                self._send_response(raw)

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else b""
                b64_body = base64.b64encode(body).decode("utf-8")
                cmd = f"echo '{b64_body}' | base64 -d | curl -s -X POST http://127.0.0.1:8000{self.path} -H 'Content-Type: application/json' --data-binary @-"
                raw = session.execute_command(cmd, timeout=120.0)
                self._send_response(raw)

            def _send_response(self, content_str: str, status_code: int = 200):
                body_bytes = content_str.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

        # Allow port reuse
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", self.port), ProxyHandler) as httpd:
            httpd.serve_forever()
