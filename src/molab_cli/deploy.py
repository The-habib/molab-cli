"""
Autonomous 1-Click Production Model Deployment Engine for MoLab Blackwell GPU.
Provides zero-friction model deployment with verified hyperparameters for NVIDIA RTX PRO 6000 (96GB GDDR7),
automated process supervision, health probing, and copy-paste credential generation.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from molab_cli.sandbox import SandboxSession
from molab_cli.tunnel import PublicTunnelManager, render_credentials

console = Console()

MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "qwen-32b": {
        "model_id": "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
        "description": "Uncensored Flagship Coding & Agent Model",
        "dtype": "bfloat16",
        "max_model_len": 65536,
        "gpu_memory_utilization": 0.88,
        "tool_call_parser": "hermes",
        "enable_auto_tool_choice": True,
    },
    "coder-32b": {
        "model_id": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "description": "Specialized Software Development & Refactoring Model",
        "dtype": "bfloat16",
        "max_model_len": 65536,
        "gpu_memory_utilization": 0.88,
        "tool_call_parser": "qwen",
        "enable_auto_tool_choice": True,
    },
    "r1-32b": {
        "model_id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        "description": "Deep Reasoning & Chain-of-Thought Mathematics Model",
        "dtype": "bfloat16",
        "max_model_len": 65536,
        "gpu_memory_utilization": 0.90,
        "tool_call_parser": None,
        "enable_auto_tool_choice": False,
    },
    "llama-70b": {
        "model_id": "neuralmagic/Meta-Llama-3.3-70B-Instruct-FP8",
        "description": "Frontier 70B Dense Model (Native FP8 Tensor Cores)",
        "dtype": "bfloat16",
        "quantization": "fp8",
        "max_model_len": 32768,
        "gpu_memory_utilization": 0.92,
        "tool_call_parser": "llama3_json",
        "enable_auto_tool_choice": True,
    },
}

# Aliases
MODEL_CATALOG["default"] = MODEL_CATALOG["qwen-32b"]
MODEL_CATALOG["qwen"] = MODEL_CATALOG["qwen-32b"]
MODEL_CATALOG["coder"] = MODEL_CATALOG["coder-32b"]
MODEL_CATALOG["r1"] = MODEL_CATALOG["r1-32b"]
MODEL_CATALOG["deepseek-r1"] = MODEL_CATALOG["r1-32b"]
MODEL_CATALOG["llama70b"] = MODEL_CATALOG["llama-70b"]


def resolve_model_spec(target: str) -> Dict[str, Any]:
    """Resolve friendly alias or raw Hugging Face model identifier into launch spec."""
    normalized = target.strip().lower()
    if normalized in MODEL_CATALOG:
        return dict(MODEL_CATALOG[normalized])

    # Custom model identifier
    return {
        "model_id": target.strip(),
        "description": f"Custom Neural Model: {target.strip()}",
        "dtype": "bfloat16",
        "max_model_len": 32768,
        "gpu_memory_utilization": 0.88,
        "tool_call_parser": "hermes",
        "enable_auto_tool_choice": True,
    }


def deploy_model_on_pod(
    model_alias: str = "qwen-32b",
    pod_id: Optional[str] = None,
    tunnel_token: Optional[str] = None,
    skip_tunnel: bool = False,
) -> Dict[str, Any]:
    """
    Execute 1-click autonomous deployment of model on Blackwell GPU pod.
    Returns dictionary containing pod status, model spec, and endpoint credentials.
    """
    spec = resolve_model_spec(model_alias)
    model_id = spec["model_id"]

    # 1. Discover or validate target pod
    if not pod_id:
        pod_id = SandboxSession.discover_active_pod()
    if not pod_id:
        raise RuntimeError("No active MoLab Blackwell GPU pod found. Provision one with 'molab create --blackwell'.")

    session = SandboxSession(pod_id)

    # 2. Check if the requested model is already online on the pod
    active_model = session.get_active_model()
    is_already_active = active_model and (active_model == model_id or model_id.split("/")[-1] in active_model)

    if not is_already_active:
        console.print(f"[bold cyan]Deploying model [{model_id}] onto Blackwell pod [{pod_id}]...[/bold cyan]")

        # Build optimized launch script on the pod
        tool_flag = f"--tool-call-parser {spec['tool_call_parser']}" if spec.get("tool_call_parser") else ""
        auto_tool_flag = "--enable-auto-tool-choice" if spec.get("enable_auto_tool_choice") else ""
        quant_flag = f"--quantization {spec['quantization']}" if spec.get("quantization") else ""

        supervisor_script = f"""#!/usr/bin/env python3
import os, sys, time, subprocess

MODEL = "{model_id}"
PORT = 8000
MAX_MODEL_LEN = {spec['max_model_len']}
GPU_UTIL = {spec['gpu_memory_utilization']}
LOG_FILE = "/workspace/qwen_server.log"
PYTHON_BIN = "/tmp/uv-venv/bin/python3"

os.environ["HF_HOME"] = "/workspace/.cache/huggingface"
os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
os.environ["CCCL_DISABLE_CTK_COMPATIBILITY_CHECK"] = "1"
os.environ["VLLM_ALLOW_LONG_MAX_MODEL_LEN"] = "1"
os.environ["VLLM_ATTENTION_BACKEND"] = "FLASH_ATTN"
os.environ["LD_LIBRARY_PATH"] = "/usr/local/lib/python3.13/site-packages/nvidia/cu13/lib:/usr/local/lib/python3.13/site-packages/nvidia/cu13/lib64:" + os.environ.get("LD_LIBRARY_PATH", "")

cmd = [
    PYTHON_BIN, "-m", "vllm.entrypoints.openai.api_server",
    "--model", MODEL,
    "--dtype", "{spec['dtype']}",
    "--port", str(PORT),
    "--max-model-len", str(MAX_MODEL_LEN),
    "--gpu-memory-utilization", str(GPU_UTIL),
    "--tensor-parallel-size", "1",
    "--trust-remote-code",
    "--host", "0.0.0.0"
]
{f'cmd.extend(["--quantization", "{spec.get("quantization")}"])' if spec.get("quantization") else ""}
{f'cmd.extend(["--tool-call-parser", "{spec.get("tool_call_parser")}"])' if spec.get("tool_call_parser") else ""}
{f'cmd.append("--enable-auto-tool-choice")' if spec.get("enable_auto_tool_choice") else ""}

def main():
    while True:
        with open(LOG_FILE, "a") as lf:
            lf.write(f"\\n=== LAUNCHING {{MODEL}} at {{time.asctime()}} ===\\n")
            lf.flush()
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
            rc = proc.wait()
            lf.write(f"\\n=== EXITED WITH CODE {{rc}} at {{time.asctime()}} (restarting in 5s) ===\\n")
            lf.flush()
        time.sleep(5)

if __name__ == "__main__":
    main()
"""
        session.upload_text(supervisor_script, "/workspace/run_qwen.py")
        session.execute_command("chmod +x /workspace/run_qwen.py ; pkill -f vllm ; nohup python3 /workspace/run_qwen.py > /workspace/qwen_server.log 2>&1 &")

        # Wait for /health
        deadline = time.time() + 180.0
        online = False
        while time.time() < deadline:
            time.sleep(4.0)
            status_code = session.execute_command("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null").strip()
            if status_code in ("200", "OK") or "healthy" in status_code or session.get_active_model():
                online = True
                break
        if not online:
            raise RuntimeError("Model server deployment timed out while loading weights on the pod.")
    else:
        console.print(f"[bold green]✔ Model [{model_id}] is already online on pod [{pod_id}].[/bold green]")

    # 3. Ensure local bridge daemon is running on port 8000
    from molab_cli.chat import ensure_claude_bridge
    ensure_claude_bridge(8000, notebook_id=pod_id)

    # 4. Create public ingress tunnel if requested
    tunnel_state = None
    if not skip_tunnel:
        tunnel_mgr = PublicTunnelManager()
        active = tunnel_mgr.get_active_tunnel()
        if not active:
            try:
                tunnel_state = tunnel_mgr.start_tunnel(port=8000, tunnel_token=tunnel_token)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not start public tunnel automatically ({e}). Endpoint available locally on port 8000.[/yellow]")
        else:
            tunnel_state = active

    return {
        "status": "ready",
        "pod_id": pod_id,
        "model_id": model_id,
        "spec": spec,
        "tunnel": tunnel_state,
        "local_url": "http://127.0.0.1:8000/v1",
        "api_key": "sk-molab-blackwell-cluster",
    }
