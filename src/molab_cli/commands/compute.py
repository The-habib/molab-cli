"""
Pod compute lifecycle, inspection, GPU telemetry, and process management commands.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import click
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from molab_cli.auth import inspect_auth_status
from molab_cli.client import MoLabClient
from molab_cli.config import get_client_cookie, set_client_cookie
from molab_cli.exceptions import ValidationError
from molab_cli.sandbox import SandboxSession, LocalHttpForwarder, resolve_target_notebook
from molab_cli.theme import (
    handle_cli_error,
    render_error_card,
    render_success_card,
    render_warning_card,
    render_info_card,
    render_status_badge,
    render_hw_badge,
    render_page_header,
)

console = Console()


@click.command("list")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output notebooks list as JSON")
def cmd_list(as_json: bool):
    """List all cloud notebooks in your workspace."""
    client = MoLabClient()
    if not as_json:
        with console.status("[bold blue]Fetching cloud notebooks from molab.marimo.io...[/bold blue]"):
            try:
                notebooks = client.list_notebooks()
            except Exception as e:
                console.print(f"[red]Error fetching notebooks:[/red] {e}")
                return
    else:
        try:
            notebooks = client.list_notebooks()
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return

    if as_json:
        print(json.dumps(notebooks, indent=2))
        return

    if not notebooks:
        console.print("[yellow]No notebooks found in your workspace.[/yellow]")
        return

    table = Table(title=f"MoLab Cloud Notebooks ({len(notebooks)} total)", box=box.ROUNDED)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Notebook ID", style="bold cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Compute Hardware", style="green")

    for nb in notebooks:
        st = "[bold green]🟢 RUNNING[/bold green]" if nb.get("running") else "[dim]⚪ STOPPED[/dim]"
        hw = "[bold green]RTX PRO 6000 (Blackwell 96GB)[/bold green]" if nb["gpu"] == "rtxp6000" else "Standard CPU"
        table.add_row(st, nb["id"], nb["title"], hw)

    console.print(table)
    console.print("\n[dim]Commands: [bold]molabctl shell <id>[/bold] | [bold]molabctl gpu <id>[/bold] | [bold]molabctl stop <id>[/bold] | [bold]molabctl ps[/bold][/dim]")



@click.command("create")
@click.option("--code", type=click.Path(exists=True), help="Local python file to upload")
@click.option("--blackwell/--cpu-only", default=True, help="Attach NVIDIA RTX Pro 6000 Blackwell Server Edition")
@click.option("--cpu", default=4, help="CPU cores (default 4)")
@click.option("--memory", default=32, help="RAM in GiB (default 32)")
@click.option("--title", help="Optional notebook title")
def cmd_create(code: Optional[str], blackwell: bool, cpu: int, memory: int, title: Optional[str]):
    """Create a new cloud notebook sandbox (with NVIDIA Blackwell GPU by default)."""
    code_content = "import marimo as mo\n"
    if code:
        code_content = Path(code).read_text(encoding="utf-8")

    gpu_type = "rtxp6000" if blackwell else ""
    gpu_cnt = 1 if blackwell else 0

    client = MoLabClient()
    hw_desc = "NVIDIA RTX PRO 6000 Blackwell (96GB VRAM)" if blackwell else "CPU Only"
    with console.status(f"[bold green]Provisioning MoLab sandbox on CoreWeave ({hw_desc})...[/bold green]"):
        try:
            nb_id = client.create_notebook(
                code=code_content,
                gpu=gpu_type,
                cpu=cpu,
                memory=memory,
                gpu_count=gpu_cnt,
            )
            if blackwell:
                client.update_compute(nb_id, gpu="rtxp6000", cpu=cpu, memory=memory, gpu_count=1)
        except Exception as e:
            console.print(f"[red]Error creating notebook:[/red] {e}")
            return

    if title:
        try:
            client.rename_notebook(nb_id, title)
        except Exception:
            pass

    console.print(Panel(
        f"[bold green]Notebook Created Successfully![/bold green]\n\n"
        f"[bold]ID:[/bold] {nb_id}\n"
        f"[bold]Hardware:[/bold] {hw_desc}\n"
        f"[bold]CPU/RAM:[/bold] {cpu} Cores / {memory} GiB RAM\n"
        f"[bold]URL:[/bold] https://molab.marimo.io/notebooks/{nb_id}\n\n"
        f"[dim]To start root shell: [bold]molabctl shell {nb_id}[/bold][/dim]\n"
        f"[dim]To run command:     [bold]molabctl exec {nb_id} 'nvidia-smi'[/bold][/dim]",
        title="New Cloud Sandbox",
        border_style="green",
    ))



@click.command("compute")
@click.argument("notebook_id", required=False)
@click.option("--blackwell/--cpu-only", "use_blackwell", default=True, help="Select NVIDIA RTX Pro 6000 Blackwell (96GB VRAM)")
@click.option("--cpu", default=4, help="CPU cores")
@click.option("--memory", default=32, help="Memory GiB")
def cmd_compute(notebook_id: Optional[str], use_blackwell: bool, cpu: int, memory: int):
    """Switch notebook compute resources to NVIDIA Blackwell or CPU."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Compute Target Resolution Failed")
        return

    gpu = "rtxp6000" if use_blackwell else ""
    gpu_cnt = 1 if use_blackwell else 0
    hw_label = "NVIDIA RTX Pro 6000 Blackwell (96GB VRAM)" if use_blackwell else "CPU Only"

    with console.status(f"[bold yellow]Configuring compute to {hw_label} and restarting sandbox...[/bold yellow]"):
        try:
            res = client.update_compute(target_nb, gpu=gpu, cpu=cpu, memory=memory, gpu_count=gpu_cnt)
            console.print(f"[green]✔ Compute updated successfully![/green]")
            console.print(f"  [bold]Hardware:[/bold] {hw_label}")
            console.print(f"  [bold]CPU/Memory:[/bold] {cpu} Cores / {memory} GiB RAM")
            if res.get("new_sandbox_id"):
                console.print(f"  [bold]New Sandbox Pod:[/bold] {res.get('new_sandbox_id')}")
        except Exception as e:
            handle_cli_error(e, title="Compute Update Failed")



@click.command("inspect")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output inspection as JSON")
def cmd_inspect(notebook_id: Optional[str], as_json: bool):
    """Inspect notebook configuration, sandbox pod status, and hardware specs."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Inspection Resolution Failed", as_json=as_json)
        return

    session = SandboxSession(target_nb, client=client)

    if not as_json:
        with console.status("[bold blue]Connecting to MoLab pod...[/bold blue]"):
            try:
                info = client.inspect_notebook(target_nb)
                health = session.check_health()
            except Exception as e:
                handle_cli_error(e, title="Error Inspecting Notebook", as_json=as_json)
                return
    else:
        try:
            info = client.inspect_notebook(target_nb)
            health = session.check_health()
        except Exception as e:
            handle_cli_error(e, title="Error Inspecting Notebook", as_json=True)
            return

    if as_json:
        print(json.dumps({"notebook": info, "health": health}, indent=2))
        return

    table = Table(title=f"Notebook: {info['title']} ({target_nb})", box=box.ROUNDED)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="magenta")

    table.add_row("Notebook ID", info["id"])
    table.add_row("Title", info["title"])
    table.add_row("CoreWeave Sandbox ID", info.get("sandbox_id", "N/A"))
    table.add_row("Sandbox Health", f"[bold green]{health.get('status', 'unknown')}[/bold green]")
    table.add_row("Python Version", health.get("python_version", "N/A"))
    table.add_row("Marimo Kernel Version", health.get("version", "N/A"))

    gpu_str = "[bold green]NVIDIA RTX Pro 6000 Blackwell (96GB VRAM)[/bold green]" if info["gpu"] == "rtxp6000" else "None (CPU)"
    table.add_row("Configured GPU", gpu_str)
    table.add_row("Configured CPU / RAM", f"{info['cpu']} Cores / {info['memory']} GiB")
    table.add_row("Web URL", info["url"])

    console.print(table)



@click.command("ps")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output active pods as JSON")
def cmd_ps(as_json: bool):
    """List actively running cloud sandbox pods."""
    client = MoLabClient()
    if not as_json:
        with console.status("[bold blue]Querying running CoreWeave sandbox pods...[/bold blue]"):
            try:
                running_map = client.list_running_sandboxes()
                notebooks = {nb["id"]: nb for nb in client.list_notebooks()}
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                return
    else:
        try:
            running_map = client.list_running_sandboxes()
            notebooks = {nb["id"]: nb for nb in client.list_notebooks()}
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return

    if as_json:
        pods_list = [
            {
                "notebook_id": nb_id,
                "sandbox_id": sb_id,
                "title": notebooks.get(nb_id, {}).get("title", "Unknown"),
                "gpu": notebooks.get(nb_id, {}).get("gpu", ""),
                "is_blackwell": notebooks.get(nb_id, {}).get("gpu") == "rtxp6000",
            }
            for nb_id, sb_id in running_map.items()
        ]
        print(json.dumps(pods_list, indent=2))
        return

    if not running_map:
        console.print("[yellow]No active sandbox pods running.[/yellow]")
        return

    table = Table(title=f"Active Cloud Pods ({len(running_map)} running)", box=box.ROUNDED)
    table.add_column("Notebook ID", style="bold cyan", no_wrap=True)
    table.add_column("Sandbox Pod ID", style="bold magenta", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Compute", style="green")

    for nb_id, sb_id in running_map.items():
        nb = notebooks.get(nb_id, {})
        title = nb.get("title", "Unknown")
        hw = "[bold green]RTX PRO 6000 (Blackwell 96GB)[/bold green]" if nb.get("gpu") == "rtxp6000" else "Standard CPU"
        table.add_row(nb_id, sb_id, title, hw)

    console.print(table)



@click.command("free")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output free pod audit as JSON")
def cmd_free(as_json: bool):
    """Find and identify free/idle Blackwell GPU pods without disturbing occupied pods."""
    client = MoLabClient()
    if not as_json:
        with console.status("[bold blue]Scanning active Blackwell GPU pods...[/bold blue]"):
            try:
                running_map = client.list_running_sandboxes()
                notebooks = {nb["id"]: nb for nb in client.list_notebooks()}
            except Exception as e:
                console.print(f"[red]Error scanning pods:[/red] {e}")
                return
    else:
        try:
            running_map = client.list_running_sandboxes()
            notebooks = {nb["id"]: nb for nb in client.list_notebooks()}
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return

    blackwell_running = [nb_id for nb_id in running_map if notebooks.get(nb_id, {}).get("gpu") == "rtxp6000"]

    if not blackwell_running:
        msg = {"error": "No running Blackwell GPU pods found.", "running_pods": list(running_map.keys())}
        if as_json:
            print(json.dumps(msg))
        else:
            console.print("[yellow]No running Blackwell GPU pods found.[/yellow]")
            console.print("[dim]Use 'molab create' or 'molab compute <id> --blackwell' to launch one.[/dim]")
        return

    pod_reports = []
    recommended_free_id = None

    for nb_id in blackwell_running:
        nb_title = notebooks.get(nb_id, {}).get("title", "Untitled")
        try:
            session = SandboxSession(nb_id, client=client)
            status = session.get_workload_status()
            status["title"] = nb_title
            pod_reports.append(status)
            if not status["is_occupied"] and recommended_free_id is None:
                recommended_free_id = nb_id
        except Exception as err:
            pod_reports.append({
                "notebook_id": nb_id,
                "title": nb_title,
                "error": str(err),
                "is_occupied": True,
                "status": "UNREACHABLE"
            })

    if not recommended_free_id:
        valid_pods = [p for p in pod_reports if "free_vram_gb" in p]
        if valid_pods:
            valid_pods.sort(key=lambda x: x.get("free_vram_gb", 0), reverse=True)
            recommended_free_id = valid_pods[0]["notebook_id"]

    result_data = {
        "recommended_free_pod": recommended_free_id,
        "total_blackwell_running": len(blackwell_running),
        "pods": pod_reports,
    }

    if as_json:
        print(json.dumps(result_data, indent=2))
        return

    table = Table(title="Blackwell GPU Pod Workload Audit", box=box.ROUNDED)
    table.add_column("Notebook ID", style="bold cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("VRAM (Alloc/Free)", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Recommendation", style="bold yellow")

    for p in pod_reports:
        nb_id = p["notebook_id"]
        title = p.get("title", "Unknown")
        if "error" in p:
            table.add_row(nb_id, title, "N/A", "[red]UNREACHABLE[/red]", "[dim]Skip[/dim]")
            continue

        vram = f"{p['allocated_vram_gb']}G / {p['free_vram_gb']}G"
        st = "[red]OCCUPIED[/red]" if p["is_occupied"] else "[bold green]FREE / IDLE[/bold green]"
        rec = "[bold green]★ RECOMMENDED[/bold green]" if nb_id == recommended_free_id else "[dim]In-Use / Occupied[/dim]"
        table.add_row(nb_id, title, vram, st, rec)

    console.print(table)
    if recommended_free_id:
        console.print(f"\n[bold green]✔ Recommended free pod for heavy compute:[/bold green] [bold cyan]{recommended_free_id}[/bold cyan]")



@click.command("stop")
@click.argument("notebook_id", required=False)
def cmd_stop(notebook_id: Optional[str]):
    """Stop/Shutdown a running cloud sandbox container pod."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Stop Target Resolution Failed")
        return

    with console.status(f"[bold yellow]Stopping cloud sandbox for {target_nb}...[/bold yellow]"):
        try:
            client.stop_notebook(target_nb)
            console.print(f"[green]✔ Cloud sandbox pod stopped for [bold]{target_nb}[/bold][/green]")
        except Exception as e:
            handle_cli_error(e, title="Stop Pod Failed")



@click.command("delete")
@click.argument("notebook_id")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
def cmd_delete(notebook_id: str, yes: bool):
    """Permanently delete a cloud notebook."""
    if not yes:
        if not click.confirm(f"Are you sure you want to permanently delete {notebook_id}?"):
            console.print("[dim]Aborted.[/dim]")
            return

    client = MoLabClient()
    with console.status(f"[bold red]Deleting notebook {notebook_id}...[/bold red]"):
        try:
            client.delete_notebook(notebook_id)
            console.print(f"[green]✔ Notebook [bold]{notebook_id}[/bold] permanently deleted.[/green]")
        except Exception as e:
            console.print(f"[red]Delete failed:[/red] {e}")



@click.command("gpu")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output GPU telemetry as JSON")
def cmd_gpu(notebook_id: Optional[str], as_json: bool):
    """Display real-time NVIDIA Blackwell GPU telemetry and VRAM utilization."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="GPU Telemetry Target Failed", as_json=as_json)
        return

    session = SandboxSession(target_nb, client=client)
    if not as_json:
        with console.status("[bold green]Querying NVIDIA Blackwell GPU telemetry...[/bold green]"):
            try:
                telemetry = session.get_gpu_telemetry()
            except Exception as e:
                handle_cli_error(e, title="Failed to query GPU telemetry", as_json=as_json)
                return
    else:
        try:
            telemetry = session.get_gpu_telemetry()
            print(json.dumps(telemetry, indent=2))
            return
        except Exception as e:
            handle_cli_error(e, title="Failed to query GPU telemetry", as_json=True)
            return

    if not telemetry.get("cuda_available"):
        console.print(Panel(
            "[yellow]No NVIDIA GPU detected on this instance (Instance is in CPU-only mode).[/yellow]\n\n"
            f"To switch this notebook to NVIDIA Blackwell GPU:\n"
            f"[bold green]molab compute {target_nb} --blackwell[/bold green]",
            title="GPU Telemetry",
            border_style="yellow"
        ))
        return

    table = Table(title="NVIDIA Blackwell GPU Telemetry", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold green")

    table.add_row("Device Model", telemetry.get("device_name", "Unknown"))
    table.add_row("Total VRAM", f"{telemetry.get('total_vram_gb')} GB GDDR7")
    table.add_row("Allocated VRAM", f"{telemetry.get('allocated_vram_gb')} GB")
    table.add_row("Streaming Multiprocessors", f"{telemetry.get('sm_count')} SMs")
    table.add_row("Compute Capability", f"sm_{telemetry.get('compute_capability', '').replace('.', '')} ({telemetry.get('compute_capability')})")
    table.add_row("CUDA Runtime", telemetry.get("cuda_version", "CUDA 13.0"))
    table.add_row("PyTorch Version", telemetry.get("torch_version", "N/A"))
    if telemetry.get("host_ram_gb"):
        table.add_row("Host System RAM", f"{telemetry.get('host_ram_gb')} GiB")
    if telemetry.get("cpu_cores"):
        table.add_row("Host CPU Cores", f"{telemetry.get('cpu_cores')} vCPUs")

    console.print(table)



@click.command("usage")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output hardware usage as JSON")
def cmd_usage(notebook_id: Optional[str], as_json: bool):
    """Display real-time host RAM, server RAM, kernel RAM, and GPU memory telemetry."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Usage Target Failed", as_json=as_json)
        return

    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(target_nb, client=client)
    backend = MarimoBackendClient(session)
    if not as_json:
        with console.status("[bold cyan]Fetching real-time backend usage...[/bold cyan]"):
            try:
                usage = backend.get_usage()
            except Exception as e:
                console.print(f"[red]Error querying usage:[/red] {e}")
                return
    else:
        try:
            usage = backend.get_usage()
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return
        print(json.dumps(usage, indent=2))
        return

    table = Table(title=f"MoLab Backend Telemetry ({target_nb})", box=box.ROUNDED)
    table.add_column("Resource", style="cyan")
    table.add_column("Allocation", style="bold white")
    table.add_column("Details / Percentage", style="green")

    table.add_row("Host Cgroup RAM", f"{usage['used_gb']} GB / {usage['total_gb']} GB", f"{usage['percent_used']}% used ({usage['free_gb']} GB available)")
    table.add_row("Server Process RAM", f"{usage['server_memory_mb']} MB", "Marimo Web & API Server")
    table.add_row("Kernel Process RAM", f"{usage['kernel_memory_mb']} MB", "Marimo Python Kernel")
    table.add_row("Host CPU Usage", f"{usage['cpu_percent']}%", "Host container CPU")

    for g in usage.get("gpus", []):
        table.add_row(
            f"GPU {g['index']} ({g['name']})",
            f"{g['used_gb']} GB / {g['total_gb']} GB",
            f"{g['percent_used']}% used ({g['free_gb']} GB free)",
        )

    console.print(table)



@click.command("env")
@click.argument("notebook_id", required=False)
@click.option("--as-json", is_flag=True, help="Output JSON format")
def cmd_remote_env(notebook_id: Optional[str], as_json: bool):
    """Inspect remote pod environment specifications (OS, gVisor, Python, Node, uv, dependencies)."""
    from molab_cli.backend import MarimoBackendClient
    from molab_cli.client import MoLabClient
    from molab_cli.sandbox import SandboxSession

    target_nb = notebook_id
    if not target_nb:
        client = MoLabClient()
        running = client.list_running_sandboxes()
        if not running:
            console.print("[red]No running pods found.[/red]")
            return
        target_nb = list(running.keys())[0]

    target_nb = target_nb if target_nb.startswith("nb_") else f"nb_{target_nb}"
    session = SandboxSession(target_nb)
    backend = MarimoBackendClient(session)

    with console.status("[bold cyan]Querying remote environment metadata...[/bold cyan]"):
        try:
            env = backend.get_environment()
            if as_json:
                console.print(json.dumps(env, indent=2))
                return

            from rich.table import Table
            table = Table(title=f"Remote Environment: {target_nb} ({session.sandbox_id})", border_style="cyan")
            table.add_column("Property", style="bold white")
            table.add_column("Value", style="green")

            table.add_row("Operating System", f"{env.get('OS', 'Linux')} ({env.get('OS Version', '')})")
            table.add_row("Python Runtime", env.get("Python Version", "Unknown"))
            table.add_row("Node.js Runtime", env.get("Binaries", {}).get("Node", "Unknown"))
            table.add_row("uv Package Manager", env.get("Binaries", {}).get("uv", "Unknown"))
            table.add_row("CUDA Index", "https://pypi.nvidia.com (Configured)")

            opt_deps = env.get("Optional Dependencies", {})
            if opt_deps:
                deps_summary = ", ".join([f"{k} ({v})" for k, v in list(opt_deps.items())[:10]])
                table.add_row("Key AI/ML Libraries", deps_summary)

            console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to query environment:[/red] {e}")



@click.command("thumbnail")
@click.argument("notebook_id")
@click.option("-o", "--output", default="thumbnail.svg", help="Output file path (default: thumbnail.svg)")
def cmd_thumbnail(notebook_id: str, output: str):
    """Generate and download visual Open Graph SVG thumbnail of the remote notebook."""
    from molab_cli.backend import MarimoBackendClient
    from molab_cli.sandbox import SandboxSession

    target_nb = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
    session = SandboxSession(target_nb)
    backend = MarimoBackendClient(session)

    with console.status(f"[bold cyan]Generating visual SVG thumbnail for {target_nb}...[/bold cyan]"):
        try:
            backend.get_thumbnail(output_path=output)
            console.print(f"[bold green]✔ Thumbnail successfully generated and saved to {output}![/bold green]")
        except Exception as e:
            console.print(f"[red]Failed to generate thumbnail:[/red] {e}")



@click.command("connections")
@click.argument("notebook_id")
@click.option("--as-json", is_flag=True)
def cmd_connections(notebook_id: str, as_json: bool):
    """Audit active WebSocket client connections to the remote Marimo server."""
    from molab_cli.backend import MarimoBackendClient
    from molab_cli.sandbox import SandboxSession

    target_nb = notebook_id if notebook_id.startswith("nb_") else f"nb_{notebook_id}"
    session = SandboxSession(target_nb)
    backend = MarimoBackendClient(session)

    try:
        conns = backend.get_connections()
        if as_json:
            console.print(json.dumps(conns, indent=2))
        else:
            console.print(f"Active Client Connections for [bold]{target_nb}[/bold]: [bold cyan]{conns.get('active', 0)}[/bold cyan]")
    except Exception as e:
        console.print(f"[red]Failed to query connections:[/red] {e}")



COMPUTE_COMMANDS = [
    cmd_list,
    cmd_create,
    cmd_compute,
    cmd_inspect,
    cmd_ps,
    cmd_free,
    cmd_stop,
    cmd_delete,
    cmd_gpu,
    cmd_usage,
    cmd_remote_env,
    cmd_thumbnail,
    cmd_connections,
]
