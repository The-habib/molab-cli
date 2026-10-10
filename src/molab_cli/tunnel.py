"""
Public Tunnel and Credentials Sharing Engine for MoLab Cloud GPU Models.
Generates instant, zero-cost, globally accessible HTTPS endpoints via Cloudflare Quick Tunnels
for OpenAI-compatible and Anthropic-compatible clients anywhere on the internet.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

TUNNEL_CONFIG_DIR = os.path.expanduser("~/.config/molab")
TUNNEL_STATE_FILE = os.path.join(TUNNEL_CONFIG_DIR, "public_tunnel.json")


class PublicTunnelManager:
    """Manages public Cloudflare tunnels and formats client credentials."""

    def __init__(self, config_file: str = TUNNEL_STATE_FILE):
        self.config_file = config_file
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

    @staticmethod
    def is_cloudflared_installed() -> bool:
        """Check if cloudflared binary is available in PATH."""
        return shutil.which("cloudflared") is not None

    def get_active_tunnel(self) -> Optional[Dict[str, Any]]:
        """Return active tunnel metadata if process is alive."""
        if not os.path.exists(self.config_file):
            return None
        try:
            with open(self.config_file, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid and self._is_pid_alive(pid):
                return data
            else:
                self.cleanup()
                return None
        except Exception:
            return None

    def _is_pid_alive(self, pid: int) -> bool:
        """Check if process with given PID exists."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def cleanup(self) -> None:
        """Remove state file."""
        if os.path.exists(self.config_file):
            try:
                os.remove(self.config_file)
            except OSError:
                pass

    def stop(self) -> bool:
        """Stop any running public tunnel process."""
        active = self.get_active_tunnel()
        if not active:
            self.cleanup()
            # Also catch any stray cloudflared processes
            subprocess.run(["pkill", "-f", "cloudflared tunnel"], stderr=subprocess.DEVNULL)
            return False

        pid = active.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                if self._is_pid_alive(pid):
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        subprocess.run(["pkill", "-f", "cloudflared tunnel"], stderr=subprocess.DEVNULL)
        self.cleanup()
        return True

    def start_tunnel(
        self,
        port: int = 8000,
        background: bool = True,
        timeout: float = 15.0,
        tunnel_token: Optional[str] = None,
        hostname: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Launch Cloudflare Quick Tunnel or Named Tunnel forwarding to local port.
        Supports custom domain with tunnel_token and hostname.
        Returns dictionary with URL and credentials metadata.
        """
        if not self.is_cloudflared_installed():
            raise RuntimeError(
                "cloudflared is not installed. Run 'pkg install cloudflared' in Termux to install it."
            )

        # Stop existing tunnel if any
        self.stop()

        # Probe model server health to discover loaded model
        model_name = "huihui-ai/Qwen2.5-32B-Instruct-abliterated"
        pod_id = "unknown"
        hardware = "NVIDIA RTX PRO 6000 Blackwell (94.97 GB GDDR7)"
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                hdata = json.loads(resp.read().decode("utf-8"))
                model_name = hdata.get("model") or model_name
                pod_id = hdata.get("pod_id") or pod_id
                hardware = hdata.get("hardware") or hardware
        except Exception:
            pass

        log_path = os.path.join(TUNNEL_CONFIG_DIR, "tunnel.log")
        log_file = open(log_path, "w")

        if tunnel_token:
            cmd = [
                "cloudflared",
                "tunnel",
                "run",
                "--token",
                tunnel_token,
            ]
        else:
            cmd = [
                "cloudflared",
                "tunnel",
                "--url",
                f"http://127.0.0.1:{port}",
                "--metrics",
                "127.0.0.1:0",
            ]

        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        public_url = None
        deadline = time.time() + timeout

        if tunnel_token:
            public_url = (hostname if hostname.startswith("http") else f"https://{hostname}") if hostname else None
            named_conn_pattern = re.compile(r"Registered tunnel connection|Connection [a-f0-9\-]+ registered")
            while time.time() < deadline:
                time.sleep(0.5)
                if proc.poll() is not None:
                    log_file.close()
                    with open(log_path, "r") as rf:
                        err = rf.read()
                    raise RuntimeError(f"cloudflared named tunnel exited unexpectedly: {err}")
                if os.path.exists(log_path):
                    with open(log_path, "r") as rf:
                        content = rf.read()
                        if named_conn_pattern.search(content):
                            if not public_url:
                                m = re.search(r"https://[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}", content)
                                public_url = m.group(0) if m else "https://custom-cloudflare-domain"
                            break
            if not public_url:
                public_url = "https://custom-cloudflare-domain"
        else:
            pattern = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
            while time.time() < deadline:
                time.sleep(0.5)
                if proc.poll() is not None:
                    log_file.close()
                    with open(log_path, "r") as rf:
                        err = rf.read()
                    raise RuntimeError(f"cloudflared exited unexpectedly: {err}")

                if os.path.exists(log_path):
                    with open(log_path, "r") as rf:
                        content = rf.read()
                        m = pattern.search(content)
                        if m:
                            public_url = m.group(0)
                            break

            if not public_url:
                proc.terminate()
                log_file.close()
                raise TimeoutError("Timed out waiting for Cloudflare to assign a public tunnel URL.")

        state = {
            "pid": proc.pid,
            "port": port,
            "public_url": public_url,
            "openai_base_url": f"{public_url}/v1",
            "anthropic_base_url": public_url,
            "api_key": "sk-molab-blackwell-cluster",
            "model": model_name,
            "pod_id": pod_id,
            "hardware": hardware,
            "started_at": int(time.time()),
        }

        with open(self.config_file, "w") as f:
            json.dump(state, f, indent=2)

        return state


def render_credentials(state: Dict[str, Any]) -> None:
    """Render high-craft Rich terminal panel with public credentials and integrations."""
    url = state["public_url"]
    base_url = state["openai_base_url"]
    api_key = state["api_key"]
    model = state["model"]
    hardware = state["hardware"]
    pod_id = state.get("pod_id", "active")

    content = (
        f"[bold green]🌐 Public AI Model Endpoint is Live & Globally Accessible![/bold green]\n\n"
        f"• [bold white]Public Base URL:[/bold white]       [bold cyan]{base_url}[/bold cyan]\n"
        f"• [bold white]API Key:[/bold white]               [bold yellow]{api_key}[/bold yellow] [dim](or any arbitrary key)[/dim]\n"
        f"• [bold white]Primary Model:[/bold white]         [bold green]{model}[/bold green]\n"
        f"• [bold white]Pod / Hardware:[/bold white]        [white]{pod_id}[/white] • [magenta]{hardware}[/magenta]\n\n"
        f"[bold white]Compatible Model Aliases:[/bold white]\n"
        f"  - [cyan]huihui-ai/Qwen2.5-32B-Instruct-abliterated[/cyan]\n"
        f"  - [cyan]claude-3-7-sonnet-20250219[/cyan] [dim](Claude Code & Anthropic clients)[/dim]\n"
        f"  - [cyan]claude-sonnet-4-5-20250929[/cyan]\n"
        f"  - [cyan]qwen2.5-coder-32b-abliterated[/cyan]\n\n"
        f"[bold white]Endpoints Available:[/bold white]\n"
        f"  • Chat Completions:   [dim]{base_url}/chat/completions[/dim]\n"
        f"  • Models List:        [dim]{base_url}/models[/dim]\n"
        f"  • Health Check:       [dim]{url}/health[/dim]"
    )

    console.print(Panel(content, border_style="green", title="[bold]MoLab Public Credentials[/bold]"))

    # Render client configurations table
    table = Table(title="Client Integration Cheatsheet", border_style="cyan")
    table.add_column("Client / Tool", style="bold cyan")
    table.add_column("Configuration Details", style="white")

    table.add_row(
        "Python (OpenAI SDK)",
        f"client = OpenAI(base_url='{base_url}', api_key='{api_key}')\n"
        f"resp = client.chat.completions.create(model='{model}', messages=[...])"
    )

    table.add_row(
        "cURL",
        f"curl {base_url}/chat/completions \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -H 'Authorization: Bearer {api_key}' \\\n"
        f"  -d '{{\"model\": \"{model}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hi\"}}]}}'"
    )

    table.add_row(
        "Cursor / VS Code (Cline / Continue)",
        f"Provider: OpenAI Compatible\n"
        f"Base URL: {base_url}\n"
        f"API Key:  {api_key}\n"
        f"Model:    {model}"
    )

    table.add_row(
        "Claude Code (Desktop)",
        f"export ANTHROPIC_BASE_URL='{url}'\n"
        f"export ANTHROPIC_API_KEY='{api_key}'\n"
        f"claude -p 'Hello from Claude Code on Blackwell!'"
    )

    console.print(table)
    console.print(f"\n[dim]To stop this public endpoint later, run: [bold cyan]molab share --stop[/bold cyan][/dim]")
