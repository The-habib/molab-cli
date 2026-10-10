"""
Enterprise-Grade Inference Performance Profiler & SRE Telemetry for MoLab.
Connects directly to the self-hosted vLLM engine and NVIDIA GPU on the active pod
to measure Prefix Cache Hit Rate (APC), Time To First Token (TTFT), KV Cache allocation,
and hardware thermal/memory dynamics matching big-brand AI production standards.
"""

import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from molab_cli.sandbox import SandboxSession

console = Console()


def parse_vllm_prometheus_metrics(raw_text: str) -> Dict[str, float]:
    """Parse Prometheus metrics text into key-value gauge and counter dictionary."""
    metrics: Dict[str, float] = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: metric_name{labels} value OR metric_name value
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)$", line)
        if m:
            name, val_str = m.group(1), m.group(2)
            try:
                metrics[name] = float(val_str)
            except ValueError:
                pass
    return metrics


def query_pod_performance(notebook_id: Optional[str] = None) -> Dict[str, Any]:
    """Query live vLLM engine metrics and GPU hardware metrics from the active pod."""
    if not notebook_id:
        notebook_id = SandboxSession.discover_active_pod()
    if not notebook_id:
        return {"error": "No running MoLab pod found."}

    session = SandboxSession(notebook_id)

    # 1. Query vLLM Prometheus metrics
    cmd_metrics = "curl -s --max-time 4 http://127.0.0.1:8000/metrics 2>/dev/null"
    raw_metrics = session.execute_command(cmd_metrics, timeout=6.0)
    parsed = parse_vllm_prometheus_metrics(raw_metrics)

    # 2. Query NVIDIA-SMI hardware telemetry
    cmd_smi = "nvidia-smi --query-gpu=memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits 2>/dev/null"
    raw_smi = session.execute_command(cmd_smi, timeout=6.0).strip()

    gpu_telemetry = {}
    if raw_smi and "," in raw_smi:
        parts = [p.strip() for p in raw_smi.split(",")]
        if len(parts) >= 7:
            gpu_telemetry = {
                "mem_total_mb": float(parts[0]),
                "mem_used_mb": float(parts[1]),
                "mem_free_mb": float(parts[2]),
                "gpu_util_pct": float(parts[3]),
                "temp_c": float(parts[4]),
                "power_draw_w": float(parts[5]),
                "power_limit_w": float(parts[6]),
            }

    # 3. Calculate Prefix Cache Efficiency
    prefix_queries = parsed.get("vllm:prefix_cache_queries_total", 0.0)
    prefix_hits = parsed.get("vllm:prefix_cache_hits_total", 0.0)
    cache_hit_rate = (prefix_hits / prefix_queries * 100.0) if prefix_queries > 0 else 0.0

    # 4. Calculate TTFT & Generation Statistics
    ttft_sum = parsed.get("vllm:time_to_first_token_seconds_sum", 0.0)
    ttft_count = parsed.get("vllm:time_to_first_token_seconds_count", 0.0)
    avg_ttft_ms = (ttft_sum / ttft_count * 1000.0) if ttft_count > 0 else 0.0

    gen_tokens = parsed.get("vllm:request_generation_tokens_sum", 0.0)
    running_reqs = parsed.get("vllm:num_requests_running", 0.0)
    waiting_reqs = parsed.get("vllm:num_requests_waiting", 0.0)
    gpu_cache_usage = parsed.get("vllm:gpu_cache_usage_factor", 0.0) * 100.0

    return {
        "pod_id": notebook_id,
        "engine": "vLLM V1 (Blackwell Server Edition)",
        "prefix_cache": {
            "queries": int(prefix_queries),
            "hits": int(prefix_hits),
            "hit_rate_pct": round(cache_hit_rate, 1),
            "efficiency": "OPTIMAL" if cache_hit_rate > 75.0 else ("MODERATE" if cache_hit_rate > 40.0 else "COLD"),
        },
        "latency": {
            "avg_ttft_ms": round(avg_ttft_ms, 1),
            "requests_sampled": int(ttft_count),
        },
        "throughput": {
            "total_tokens_generated": int(gen_tokens),
            "active_running_requests": int(running_reqs),
            "queued_waiting_requests": int(waiting_reqs),
            "kv_cache_usage_pct": round(gpu_cache_usage, 1),
        },
        "gpu": gpu_telemetry,
    }


def render_performance_dashboard(data: Dict[str, Any]) -> None:
    """Render high-contrast Rich telemetry dashboard for self-hosted model performance."""
    if "error" in data:
        console.print(f"[red]Error:[/red] {data['error']}")
        return

    pod_id = data.get("pod_id", "unknown")
    cache = data.get("prefix_cache", {})
    lat = data.get("latency", {})
    tp = data.get("throughput", {})
    gpu = data.get("gpu", {})

    hit_rate = cache.get("hit_rate_pct", 0.0)
    hit_color = "bold green" if hit_rate > 75.0 else ("bold yellow" if hit_rate > 40.0 else "bold red")

    # Card 1: Prefix Caching & Engine Efficiency
    cache_panel = (
        f"• [bold white]Prefix Cache Hit Rate:[/bold white]   [{hit_color}]{hit_rate}% ({cache.get('efficiency')})[/{hit_color}]\n"
        f"• [bold white]Cached Tokens Reused:[/bold white]    [green]{cache.get('hits', 0):,}[/green] / {cache.get('queries', 0):,} queried\n"
        f"• [bold white]Average Time To First Token:[/bold white] [bold cyan]{lat.get('avg_ttft_ms', 0.0)} ms[/bold cyan] [dim]({lat.get('requests_sampled', 0)} requests sampled)[/dim]\n"
        f"• [bold white]KV Cache Block Headroom:[/bold white]     [white]{tp.get('kv_cache_usage_pct', 0.0)}% utilized[/white]\n"
        f"• [bold white]Active Concurrency:[/bold white]          [cyan]{tp.get('active_running_requests', 0)} running[/cyan], [yellow]{tp.get('queued_waiting_requests', 0)} waiting[/yellow]\n"
        f"• [bold white]Total Generated Tokens:[/bold white]      [bold green]{tp.get('total_tokens_generated', 0):,}[/bold green]"
    )
    console.print(Panel(
        cache_panel,
        title="[bold #00BD7D]◆ ENGINE TELEMETRY • Automatic Prefix Caching (APC)[/bold #00BD7D]",
        border_style="#00BD7D",
    ))

    # Card 2: GPU Hardware Telemetry
    if gpu:
        mem_used_gb = gpu.get("mem_used_mb", 0) / 1024.0
        mem_tot_gb = gpu.get("mem_total_mb", 0) / 1024.0
        pwr_draw = gpu.get("power_draw_w", 0)
        pwr_lim = gpu.get("power_limit_w", 600)
        temp = gpu.get("temp_c", 0)

        gpu_panel = (
            f"• [bold white]Compute Core:[/bold white]        [yellow]NVIDIA RTX PRO 6000 Blackwell (sm_120, 94.97 GB GDDR7)[/yellow]\n"
            f"• [bold white]VRAM Memory:[/bold white]         [bold green]{mem_used_gb:.2f} GB[/bold green] used / [white]{mem_tot_gb:.2f} GB total[/white] [dim]({gpu.get('mem_free_mb', 0)/1024:.2f} GB free)[/dim]\n"
            f"• [bold white]GPU Core Utilization:[/bold white] [cyan]{gpu.get('gpu_util_pct', 0):.0f}%[/cyan]\n"
            f"• [bold white]Temperature & Power:[/bold white]  [green]{temp:.0f}°C[/green] • [white]{pwr_draw:.1f} W[/white] / {pwr_lim:.0f} W\n"
            f"• [bold white]Architecture Mode:[/bold white]    [dim]Bfloat16 + FlashInfer Kernel + Continuous Chunked Prefill[/dim]"
        )
        console.print(Panel(
            gpu_panel,
            title="[bold yellow]⚡ HARDWARE INFRASTRUCTURE • NVIDIA Blackwell Server Edition[/bold yellow]",
            border_style="yellow",
        ))
    else:
        console.print("[dim]Hardware GPU telemetry not directly available on pod.[/dim]")
