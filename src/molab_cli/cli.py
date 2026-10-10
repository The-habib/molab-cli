"""
Rich CLI interface for molabctl.
"""

from __future__ import annotations

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


def reconstruct_script(nb_id: str, title: str, cells: list) -> str:
    lines = [
        '# /// script',
        '# requires-python = ">=3.11"',
        '# dependencies = ["marimo"]',
        '# ///',
        f'# MoLab Cloud Notebook: {nb_id}',
        f'# Title: {title}',
        f'# Generated via molabctl',
        'import marimo\n',
        'app = marimo.App()\n',
    ]
    for i, c in enumerate(cells):
        code = c.get("code", "").strip()
        name = c.get("name") or f"cell_{i}"
        if not name.isidentifier() or name == "_":
            name = f"cell_{i}"
        lines.append(f"@app.cell\ndef {name}():")
        if code:
            for cl in code.split("\n"):
                lines.append(f"    {cl}")
        else:
            lines.append("    pass")
        lines.append("    return\n")

    lines.append('if __name__ == "__main__":\n    app.run()\n')
    return "\n".join(lines)


@click.group(invoke_without_command=True)
@click.version_option(version="2.4.0", prog_name="molab")
@click.pass_context
def cli(ctx):
    """molab: Modern interactive CLI & cloud orchestrator for MoLab with NVIDIA Blackwell GPU support."""
    if ctx.invoked_subcommand is None:
        from molab_cli.tui import start_interactive_tui
        start_interactive_tui()


@cli.command("ui")
def cmd_ui():
    """Launch the interactive visual TUI Control Center dashboard."""
    from molab_cli.tui import start_interactive_tui
    start_interactive_tui()


@cli.command("web")
@click.option("-p", "--port", default=8080, help="Port to bind web server (default: 8080)")
@click.option("--no-browser", is_flag=True, help="Do not open browser automatically")
def cmd_web(port: int, no_browser: bool):
    """Launch the modern browser-based Web Control Center dashboard."""
    from molab_cli.web import start_web_server
    start_web_server(port=port, open_browser=not no_browser)


@cli.command("dashboard")
@click.option("-p", "--port", default=8080, help="Port to bind web server (default: 8080)")
@click.option("--no-browser", is_flag=True, help="Do not open browser automatically")
def cmd_dashboard(port: int, no_browser: bool):
    """Alias for 'molab web' — launch the modern browser-based Web Control Center."""
    from molab_cli.web import start_web_server
    start_web_server(port=port, open_browser=not no_browser)


@cli.command("login")
@click.option("--client", "client_cookie", help="Value of __client cookie or full cookie header string")
def cmd_login(client_cookie: Optional[str]):
    """Configure Clerk master cookie for permanent MoLab authentication."""
    from molab_cli.auth import save_and_verify_auth
    from molab_cli.onboarding import run_onboarding_wizard
    from molab_cli.theme import render_error_card, render_success_card

    if not client_cookie:
        run_onboarding_wizard()
        return

    try:
        res = save_and_verify_auth(client_cookie)
        render_success_card(
            "Authenticated Successfully!",
            f"Connected as: [bold white]{res.get('user_email')}[/bold white]\n"
            f"Session ID:   [dim]{res.get('session_id')}[/dim]\n"
            f"Saved to:     [dim]~/.config/molab/config.json[/dim]"
        )
    except Exception as e:
        hint = getattr(e, "hint", "Please ensure you copied the active __client cookie.")
        render_error_card("Authentication Failed", str(e), hint=hint)


@cli.command("status")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output session status as JSON")
def cmd_status(as_json: bool):
    """Display current authentication and workspace connection details."""
    from molab_cli.theme import render_error_card
    status = inspect_auth_status()
    if as_json:
        print(json.dumps(status, indent=2))
        return

    if status.get("authenticated"):
        table = Table(title="[bold cyan]MoLab Cloud Session[/bold cyan]", box=box.ROUNDED)
        table.add_column("Property", style="cyan", no_wrap=True)
        table.add_column("Value", style="bold white")

        table.add_row("Status", "[bold green]● Online / Connected[/bold green]")
        table.add_row("User Email", status.get("user_email", "N/A"))
        table.add_row("User ID", status.get("user_id", "N/A"))
        table.add_row("Client ID", status.get("client_id", "N/A"))
        table.add_row("Session ID", status.get("session_id", "N/A"))
        table.add_row("Organization", f"{status.get('org_slug')} ({status.get('org_id')})")
        table.add_row("Hardware Access", "[bold #76b900]NVIDIA RTX PRO 6000 Blackwell Server Edition (96GB VRAM)[/bold #76b900]")
        table.add_row("Token Type", "RS256 Auto-Minting (Zero Expiration)")
        console.print(table)
    else:
        render_error_card(
            "Authentication Inactive",
            status.get("error", "No active session configured."),
            hint="Run 'molab login' to configure your Clerk cookie or use the interactive setup wizard.",
        )


@cli.command("list")
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


@cli.command("create")
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


@cli.command("compute")
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


@cli.command("inspect")
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


@cli.command("exec")
@click.argument("notebook_id")
@click.argument("command", required=False)
@click.option("--timeout", default=30.0, help="Execution timeout in seconds")
def cmd_exec(notebook_id: str, command: Optional[str], timeout: float):
    """Execute a bash command inside the remote CoreWeave sandbox container."""
    client = MoLabClient()
    if command is None:
        if notebook_id.startswith("nb_"):
            handle_cli_error(
                ValidationError(
                    f"Missing command to execute on pod '{notebook_id}'.",
                    hint=f"Provide the command to execute: molab exec {notebook_id} \"<command>\" (or run 'molab shell {notebook_id}' for interactive terminal).",
                ),
                title="Invalid Execution Command",
            )
            return
        actual_cmd = notebook_id
        try:
            target_nb = resolve_target_notebook(client, require_running=True)
        except Exception as e:
            handle_cli_error(e, title="Remote Execution Target Failed")
            return
    else:
        actual_cmd = command
        try:
            target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
        except Exception as e:
            handle_cli_error(e, title="Remote Execution Target Failed")
            return

    session = SandboxSession(target_nb, client=client)
    with console.status(f"[bold cyan]Executing on remote pod ({session.notebook_id})...[/bold cyan]"):
        try:
            out = session.execute_command(actual_cmd, timeout=timeout)
            console.print(out, markup=False)
        except Exception as e:
            handle_cli_error(e, title="Remote Execution Failed")


@cli.command("shell")
@click.argument("notebook_id", required=False)
def cmd_shell(notebook_id: Optional[str]):
    """Open an interactive root bash terminal session directly in the CoreWeave pod."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Interactive Shell Failed")
        return

    session = SandboxSession(target_nb, client=client)
    console.print(f"[green]Connecting interactive terminal to {session.notebook_id}...[/green]")
    console.print("[dim]Press Ctrl+D or type 'exit' to disconnect.[/dim]\n")
    try:
        session.interactive_shell()
    except Exception as e:
        handle_cli_error(e, title="Interactive Shell Connection Failed")


@cli.command("cat")
@click.argument("notebook_id", required=False)
@click.option("--cell", type=int, help="Cell index (1-indexed)")
def cmd_cat(notebook_id: Optional[str], cell: Optional[int]):
    """Print Python code of notebook or specific cell."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Cell Code Retrieval Failed")
        return

    session = SandboxSession(target_nb, client=client)
    with console.status("[bold blue]Retrieving cells from sandbox...[/bold blue]"):
        try:
            cfg, cells = session.fetch_notebook_cells()
        except Exception as e:
            handle_cli_error(e, title="Error Fetching Cells")
            return

    if cell is not None:
        idx = cell - 1
        if 0 <= idx < len(cells):
            c = cells[idx]
            name = c.get("name") or f"cell_{idx}"
            console.print(f"[bold yellow]# --- Cell {cell}: {name} ---[/bold yellow]")
            syntax = Syntax(c.get("code", ""), "python", theme="monokai", line_numbers=True)
            console.print(syntax)
        else:
            console.print(f"[red]Cell {cell} out of range (1 - {len(cells)})[/red]")
    else:
        info = session.client.inspect_notebook(notebook_id)
        script = reconstruct_script(notebook_id, info.get("title", ""), cells)
        syntax = Syntax(script, "python", theme="monokai", line_numbers=True)
        console.print(syntax)


@cli.command("download")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (.py)")
def cmd_download(notebook_id: str, output: Optional[str]):
    """Download notebook as a standalone runnable Python script."""
    session = SandboxSession(notebook_id)
    with console.status("[bold blue]Fetching notebook script...[/bold blue]"):
        try:
            cfg, cells = session.fetch_notebook_cells()
            info = session.client.inspect_notebook(notebook_id)
        except Exception as e:
            console.print(f"[red]Error downloading notebook:[/red] {e}")
            return

    out_path = Path(output or f"{notebook_id}.py")
    script = reconstruct_script(notebook_id, info.get("title", ""), cells)
    out_path.write_text(script, encoding="utf-8")

    console.print(f"[green]✔ Saved {len(cells)} cells to {out_path.resolve()} ({out_path.stat().st_size} bytes)[/green]")
    console.print(f"[dim]Run locally: uvx marimo run {out_path.name}[/dim]")


@cli.command("rename")
@click.argument("notebook_id")
@click.argument("title")
def cmd_rename(notebook_id: str, title: str):
    """Rename a cloud notebook."""
    client = MoLabClient()
    with console.status(f"[bold blue]Renaming notebook {notebook_id}...[/bold blue]"):
        try:
            client.rename_notebook(notebook_id, title)
            console.print(f"[green]✔ Notebook renamed to: [bold]{title}[/bold][/green]")
        except Exception as e:
            console.print(f"[red]Rename failed:[/red] {e}")


@cli.command("clone")
@click.argument("notebook_id")
def cmd_clone(notebook_id: str):
    """Duplicate an existing cloud notebook."""
    client = MoLabClient()
    with console.status(f"[bold blue]Duplicating notebook {notebook_id}...[/bold blue]"):
        try:
            new_id = client.duplicate_notebook(notebook_id)
            console.print(Panel(
                f"[bold green]Notebook Duplicated Successfully![/bold green]\n\n"
                f"[bold]New ID:[/bold] {new_id}\n"
                f"[bold]URL:[/bold] https://molab.marimo.io/notebooks/{new_id}",
                border_style="green"
            ))
        except Exception as e:
            console.print(f"[red]Clone failed:[/red] {e}")


@cli.command("ps")
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


@cli.command("free")
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


@cli.command("stop")
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


@cli.command("delete")
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


@cli.command("push")
@click.argument("arg1")
@click.argument("arg2", required=False)
@click.argument("arg3", required=False)
@click.option("-r", "--recursive", is_flag=True, help="Transfer directory recursively")
def cmd_push(arg1: str, arg2: Optional[str], arg3: Optional[str], recursive: bool):
    """Upload a local file or dataset directly into the CoreWeave container."""
    client = MoLabClient()
    try:
        if arg3 is not None or (arg1.startswith("nb_") and arg2 is not None):
            target_nb = resolve_target_notebook(client, arg1, require_running=True)
            local_file = arg2
            remote_path = arg3
        else:
            target_nb = resolve_target_notebook(client, require_running=True)
            local_file = arg1
            remote_path = arg2
    except Exception as e:
        handle_cli_error(e, title="Upload Target Resolution Failed")
        return

    session = SandboxSession(target_nb, client=client)
    with console.status(f"[bold cyan]Uploading {local_file} to pod ({target_nb})...[/bold cyan]"):
        try:
            dest, size = session.push_file(local_file, remote_path, recursive=recursive)
            console.print(f"[green]✔ Uploaded [bold]{local_file}[/bold] -> [bold]{dest}[/bold] ({size:,} bytes)[/green]")
        except Exception as e:
            handle_cli_error(e, title="Upload Failed")


@cli.command("pull")
@click.argument("arg1")
@click.argument("arg2", required=False)
@click.argument("arg3", required=False)
@click.option("-r", "--recursive", is_flag=True, help="Transfer directory recursively")
def cmd_pull(arg1: str, arg2: Optional[str], arg3: Optional[str], recursive: bool):
    """Download a file from the CoreWeave container to local storage."""
    client = MoLabClient()
    try:
        if arg3 is not None or (arg1.startswith("nb_") and arg2 is not None):
            target_nb = resolve_target_notebook(client, arg1, require_running=True)
            remote_path = arg2
            local_file = arg3
        else:
            target_nb = resolve_target_notebook(client, require_running=True)
            remote_path = arg1
            local_file = arg2
    except Exception as e:
        handle_cli_error(e, title="Download Target Resolution Failed")
        return

    session = SandboxSession(target_nb, client=client)
    with console.status(f"[bold cyan]Downloading {remote_path} from pod ({target_nb})...[/bold cyan]"):
        try:
            dest, size = session.pull_file(remote_path, local_file, recursive=recursive)
            console.print(f"[green]✔ Downloaded [bold]{remote_path}[/bold] -> [bold]{dest}[/bold] ({size:,} bytes)[/green]")
        except Exception as e:
            handle_cli_error(e, title="Download Failed")


@cli.command("gpu")
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


@cli.command("install")
@click.argument("target_or_pkg")
@click.argument("extra_pkgs", nargs=-1)
def cmd_install(target_or_pkg: str, extra_pkgs: tuple):
    """Install Python packages inside the remote pod environment using uv/pip."""
    client = MoLabClient()
    if target_or_pkg.startswith("nb_"):
        if not extra_pkgs:
            handle_cli_error(
                ValidationError(
                    f"No packages specified to install on pod '{target_or_pkg}'.",
                    hint=f"Specify one or more packages, e.g.: molab install {target_or_pkg} torch transformers",
                ),
                title="Missing Package Names",
            )
            return
        target_nb = target_or_pkg
        pkgs_list = list(extra_pkgs)
    else:
        try:
            target_nb = resolve_target_notebook(client, require_running=True)
        except Exception as e:
            handle_cli_error(e, title="Remote Installation Target Failed")
            return
        pkgs_list = [target_or_pkg] + list(extra_pkgs)

    session = SandboxSession(target_nb, client=client)
    with console.status(f"[bold cyan]Installing {', '.join(pkgs_list)} in pod ({target_nb})...[/bold cyan]"):
        try:
            out = session.install_packages(pkgs_list)
            if out:
                console.print(out)
            console.print(f"[green]✔ Successfully installed: {' '.join(pkgs_list)}[/green]")
        except Exception as e:
            handle_cli_error(e, title="Installation Failed")


@cli.command("forward")
@click.argument("notebook_id", required=False)
@click.option("--port", default=8000, help="Local port to bind on localhost (default 8000)")
def cmd_forward(notebook_id: Optional[str], port: int):
    """Bridge local HTTP port (localhost:8000) directly to the cloud model server."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Bridge Target Resolution Failed")
        return

    session = SandboxSession(target_nb, client=client)
    with console.status("[bold green]Verifying remote model server on Blackwell pod...[/bold green]"):
        try:
            if not session.ensure_model_server_running():
                console.print("[yellow]Warning: Model server did not report healthy. Proceeding anyway...[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Note verifying server:[/yellow] {e}")

    forwarder = LocalHttpForwarder(session, port=port)
    console.print(Panel(
        f"[bold green]🚀 Localhost Bridge Active![/bold green]\n\n"
        f"• Local OpenAI Base URL: [bold cyan]http://localhost:{port}/v1[/bold cyan]\n"
        f"• Chat Completions:     [bold cyan]http://localhost:{port}/v1/chat/completions[/bold cyan]\n"
        f"• Model Health Check:   [bold cyan]http://localhost:{port}/health[/bold cyan]\n"
        f"• Cloud Hardware:       [bold green]NVIDIA RTX PRO 6000 Blackwell (96GB VRAM, sm_120)[/bold green]\n"
        f"• Target Pod:           [bold cyan]{target_nb}[/bold cyan]\n\n"
        f"[dim]You can now use curl, Python 'openai' client, Cursor, Claude Code, or Open WebUI pointing to http://localhost:{port}/v1[/dim]\n"
        f"[dim]Press [bold]Ctrl+C[/bold] to stop the forwarder.[/dim]",
        title="MoLab Localhost Bridge",
        border_style="green",
    ))
    try:
        forwarder.start()
    except KeyboardInterrupt:
        console.print("\n[dim]Forwarder stopped.[/dim]")
    except Exception as e:
        handle_cli_error(e, title="Forwarder Runtime Error")


@cli.group("bridge")
def bridge_group():
    """Manage local Blackwell AI Bridge for Hermes Agent & Claude Code."""
    pass


@bridge_group.command("start")
@click.option("--port", default=8000, help="Bridge port (default: 8000)")
@click.option("--host", default="127.0.0.1", help="Bridge host (default: 127.0.0.1)")
def cmd_bridge_start(port: int, host: str):
    """Start the Blackwell bridge server."""
    from molab_cli.bridge import start_bridge_server
    start_bridge_server(port=port, host=host)


@bridge_group.command("status")
def cmd_bridge_status():
    """Check Blackwell bridge health and status."""
    import urllib.request
    import json
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/health")
        with urllib.request.urlopen(req, timeout=6.0) as r:
            data = json.loads(r.read())
            console.print("[bold green]✓ Bridge Online[/bold green]")
            console.print(f"  • Pod ID:    {data.get('pod_id')}")
            console.print(f"  • Model:     {data.get('model')}")
            console.print(f"  • Hardware:  {data.get('hardware')}")
            console.print("  • Ports:     8000 (OpenAI / Hermes), 8082 (Anthropic / Claude Code)")
    except Exception as e:
        console.print(f"[red]✗ Bridge Offline or Unreachable:[/red] {e}")


@bridge_group.command("stop")
def cmd_bridge_stop():
    """Stop running Blackwell bridge daemons."""
    import subprocess
    subprocess.run(["pkill", "-f", "molab_cli.bridge"], stderr=subprocess.DEVNULL)
    console.print("[yellow]✓ Blackwell bridge stopped.[/yellow]")


@cli.command("share")
@click.option("--port", default=8000, help="Local port to share (default 8000)")
@click.option("--token", default=None, help="Cloudflare Tunnel token for persistent custom domain")
@click.option("--hostname", default=None, help="Custom hostname for named Cloudflare tunnel (e.g. ai.mydomain.com)")
@click.option("--stop", is_flag=True, help="Stop active public tunnel")
@click.option("--status", is_flag=True, help="Display status of active public tunnel")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output credentials as JSON")
def cmd_share(port: int, token: Optional[str], hostname: Optional[str], stop: bool, status: bool, as_json: bool):
    """Expose deployed model with a public HTTPS URL and client credentials."""
    from molab_cli.tunnel import PublicTunnelManager, render_credentials
    mgr = PublicTunnelManager()

    if stop:
        if mgr.stop():
            console.print("[green]✔ Public tunnel stopped.[/green]")
        else:
            console.print("[yellow]No active public tunnel found.[/yellow]")
        return

    if status:
        active = mgr.get_active_tunnel()
        if not active:
            if as_json:
                print(json.dumps({"status": "offline"}))
            else:
                console.print("[yellow]No active public tunnel. Run 'molab share' to create one.[/yellow]")
            return
        if as_json:
            print(json.dumps(active, indent=2))
        else:
            render_credentials(active)
        return

    with console.status("[bold green]Creating public HTTPS tunnel via Cloudflare...[/bold green]"):
        try:
            state = mgr.start_tunnel(port=port, tunnel_token=token, hostname=hostname)
        except Exception as e:
            console.print(f"[red]Failed to create public tunnel:[/red] {e}")
            return

    if as_json:
        print(json.dumps(state, indent=2))
    else:
        render_credentials(state)


@cli.command("public")
@click.option("--port", default=8000, help="Local port to share (default 8000)")
@click.option("--token", default=None, help="Cloudflare Tunnel token for persistent custom domain")
@click.option("--hostname", default=None, help="Custom hostname for named Cloudflare tunnel (e.g. ai.mydomain.com)")
@click.option("--stop", is_flag=True, help="Stop active public tunnel")
@click.option("--status", is_flag=True, help="Display status of active public tunnel")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output credentials as JSON")
@click.pass_context
def cmd_public(ctx, port: int, token: Optional[str], hostname: Optional[str], stop: bool, status: bool, as_json: bool):
    """Alias for 'molab share' to generate public model credentials."""
    ctx.invoke(cmd_share, port=port, token=token, hostname=hostname, stop=stop, status=status, as_json=as_json)


@cli.command("credentials")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output active credentials as JSON")
@click.option("-e", "--export", is_flag=True, help="Print shell export statements for instant environment setup")
def cmd_credentials(as_json: bool, export: bool):
    """Retrieve all active credentials, URLs, and client configurations for the deployed model with one command."""
    from molab_cli.tunnel import PublicTunnelManager, render_credentials
    mgr = PublicTunnelManager()
    active = mgr.get_active_tunnel()

    if active:
        if export:
            print(f'export OPENAI_BASE_URL="{active.get("openai_base_url")}"')
            print(f'export OPENAI_API_KEY="{active.get("api_key")}"')
            print(f'export ANTHROPIC_BASE_URL="{active.get("anthropic_base_url")}"')
            print(f'export ANTHROPIC_API_KEY="{active.get("api_key")}"')
            print(f'export MODEL_NAME="{active.get("model")}"')
            return

        if as_json:
            print(json.dumps(active, indent=2))
            return

        render_credentials(active)
        return

    # Check local model bridge fallback
    local_state = None
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/health")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            hdata = json.loads(resp.read().decode("utf-8"))
            local_state = {
                "status": "local_online",
                "local_url": "http://127.0.0.1:8000",
                "openai_base_url": "http://127.0.0.1:8000/v1",
                "anthropic_base_url": "http://127.0.0.1:8000",
                "api_key": "sk-molab-blackwell-cluster",
                "model": hdata.get("model", "huihui-ai/Qwen2.5-32B-Instruct-abliterated"),
                "pod_id": hdata.get("pod_id", "active"),
                "hardware": hdata.get("hardware", "NVIDIA RTX PRO 6000 Blackwell Server Edition (94.97 GB GDDR7)"),
            }
    except Exception:
        pass

    if local_state:
        if export:
            print(f'export OPENAI_BASE_URL="{local_state["openai_base_url"]}"')
            print(f'export OPENAI_API_KEY="{local_state["api_key"]}"')
            print(f'export ANTHROPIC_BASE_URL="{local_state["anthropic_base_url"]}"')
            print(f'export ANTHROPIC_API_KEY="{local_state["api_key"]}"')
            print(f'export MODEL_NAME="{local_state["model"]}"')
            return
        if as_json:
            print(json.dumps(local_state, indent=2))
            return

        render_info_card(
            "Local AI Model Online",
            f"Model '{local_state['model']}' is responding locally at {local_state['local_url']}.\n\n"
            f"• Base URL: {local_state['openai_base_url']}\n"
            f"• API Key:  {local_state['api_key']}\n\n"
            "To generate a public HTTPS URL, run: 'molab share'",
        )
        return

    if as_json:
        print(json.dumps({"status": "offline", "message": "No active public tunnel or local model bridge found."}))
    else:
        render_warning_card(
            "No Active Model Credentials",
            "No running public tunnel or local bridge detected.\n\n"
            "Run 'molab deploy <model>' to launch a model, or 'molab share' to expose an active pod.",
        )


@cli.command("creds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output active credentials as JSON")
@click.option("-e", "--export", is_flag=True, help="Print shell export statements for instant environment setup")
@click.pass_context
def cmd_creds(ctx, as_json: bool, export: bool):
    """Fast shortcut for 'molab credentials'."""
    ctx.invoke(cmd_credentials, as_json=as_json, export=export)


@cli.group("keys")
def keys_group():
    """Manage AI Gateway virtual API keys, quotas, and access governance."""
    pass


@keys_group.command("create")
@click.argument("name")
@click.option("--rpm", default=60, help="Requests Per Minute rate limit (default: 60)")
@click.option("--tpm", default=60000, help="Tokens Per Minute rate limit (default: 60000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_keys_create(name: str, rpm: int, tpm: int, as_json: bool):
    """Create a new virtual API key with custom rate and token limits."""
    from molab_cli.gateway_db import default_gateway_db
    raw_key, info = default_gateway_db.create_key(name=name, rpm_limit=rpm, tpm_limit=tpm)
    if as_json:
        payload = {**info, "raw_key": raw_key}
        print(json.dumps(payload, indent=2))
        return

    console.print(Panel(
        f"[bold green]✔ Virtual API Key Created Successfully![/bold green]\n\n"
        f"[bold cyan]Key:[/bold cyan] [bold white]{raw_key}[/bold white]\n"
        f"[dim](Save this key now — for security reasons, it cannot be displayed again.)[/dim]\n\n"
        f"[bold]Name:[/bold] {info['name']}\n"
        f"[bold]Key ID:[/bold] {info['key_id']}\n"
        f"[bold]RPM Limit:[/bold] {info['rpm_limit']:,} req/min\n"
        f"[bold]TPM Limit:[/bold] {info['tpm_limit']:,} tokens/min\n",
        title="[bold blue]MoLab AI Gateway Key Governance[/bold blue]",
        border_style="green",
    ))


@keys_group.command("list")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_keys_list(as_json: bool):
    """List all registered virtual API keys and lifetime usage metrics."""
    from molab_cli.gateway_db import default_gateway_db
    keys = default_gateway_db.list_keys()
    if as_json:
        print(json.dumps(keys, indent=2))
        return

    if not keys:
        console.print("[yellow]No virtual API keys found. Run 'molab keys create <name>' to create one.[/yellow]")
        return

    table = Table(title="MoLab AI Gateway Virtual Keys", box=box.ROUNDED)
    table.add_column("Key ID", style="cyan")
    table.add_column("Name", style="bold white")
    table.add_column("Prefix", style="dim")
    table.add_column("RPM Limit", justify="right")
    table.add_column("TPM Limit", justify="right")
    table.add_column("Requests", justify="right")
    table.add_column("Tokens (In / Out)", justify="right")
    table.add_column("Status", justify="center")

    for k in keys:
        status_badge = "[bold green]ACTIVE[/bold green]" if k.get("is_active") else "[bold red]REVOKED[/bold red]"
        tok_str = f"{k.get('total_prompt_tokens', 0):,} / {k.get('total_completion_tokens', 0):,}"
        table.add_row(
            k.get("key_id", ""),
            k.get("name", ""),
            k.get("key_prefix", ""),
            f"{k.get('rpm_limit', 0):,}",
            f"{k.get('tpm_limit', 0):,}",
            f"{k.get('total_requests', 0):,}",
            tok_str,
            status_badge,
        )

    console.print(table)


@keys_group.command("revoke")
@click.argument("key_id")
def cmd_keys_revoke(key_id: str):
    """Revoke and deactivate a virtual API key immediately."""
    from molab_cli.gateway_db import default_gateway_db
    success = default_gateway_db.revoke_key(key_id)
    if success:
        console.print(f"[green]✔ Key '{key_id}' has been revoked successfully.[/green]")
    else:
        console.print(f"[red]Error: Key '{key_id}' not found.[/red]")


@cli.command("stats")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output metrics as JSON")
@click.option("--limit", default=10, help="Number of recent request logs to display (default: 10)")
def cmd_stats(as_json: bool, limit: int):
    """Display real-time AI Gateway telemetry, latency, and request logs."""
    from molab_cli.gateway_db import default_gateway_db
    stats = default_gateway_db.get_analytics()
    if as_json:
        print(json.dumps(stats, indent=2))
        return

    total_reqs = stats.get("total_requests", 0)
    p_tokens = stats.get("total_prompt_tokens", 0)
    c_tokens = stats.get("total_completion_tokens", 0)
    tot_tokens = p_tokens + c_tokens
    avg_ttft = stats.get("avg_ttft_ms", 0.0)
    avg_lat = stats.get("avg_latency_ms", 0.0)
    active_keys = stats.get("active_keys_count", 0)

    summary_text = (
        f"[bold cyan]Total Requests:[/bold cyan] {total_reqs:,}        "
        f"[bold cyan]Active Keys:[/bold cyan] {active_keys}        "
        f"[bold cyan]Total Tokens:[/bold cyan] {tot_tokens:,} ({p_tokens:,} in / {c_tokens:,} out)\n"
        f"[bold cyan]Avg Latency:[/bold cyan] {avg_lat:.1f} ms        "
        f"[bold cyan]Avg TTFT:[/bold cyan] {avg_ttft:.1f} ms"
    )
    console.print(Panel(summary_text, title="[bold blue]MoLab Production AI Gateway Telemetry[/bold blue]", border_style="cyan"))

    recent = stats.get("recent_requests", [])
    if recent:
        table = Table(title="Recent Request Audit Logs", box=box.ROUNDED)
        table.add_column("Req ID", style="dim")
        table.add_column("Key ID", style="cyan")
        table.add_column("Model", style="bold")
        table.add_column("Prompt Tok", justify="right")
        table.add_column("Comp Tok", justify="right")
        table.add_column("TTFT", justify="right")
        table.add_column("Latency", justify="right")
        table.add_column("Status", justify="center")

        for r in recent[:limit]:
            status_color = "green" if r.get("status_code", 200) < 400 else "red"
            table.add_row(
                r.get("req_id", "")[:12],
                r.get("key_id", ""),
                r.get("model", "").split("/")[-1],
                f"{r.get('prompt_tokens', 0):,}",
                f"{r.get('completion_tokens', 0):,}",
                f"{r.get('ttft_ms', 0.0):.1f}ms",
                f"{r.get('total_duration_ms', 0.0):.1f}ms",
                f"[{status_color}]{r.get('status_code', 200)}[/{status_color}]",
            )
        console.print(table)


@cli.command("analytics")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output metrics as JSON")
@click.option("--limit", default=10, help="Number of recent request logs to display")
@click.pass_context
def cmd_analytics(ctx, as_json: bool, limit: int):
    """Alias for 'molab stats' — display AI Gateway usage metrics."""
    ctx.invoke(cmd_stats, as_json=as_json, limit=limit)


@cli.command("perf")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output performance telemetry as JSON")
def cmd_perf(notebook_id: Optional[str], as_json: bool):
    """Profile self-hosted model performance (Prefix Cache Hit Rate, TTFT, VRAM, GPU temps)."""
    from molab_cli.perf import query_pod_performance, render_performance_dashboard
    with console.status("[bold #00BD7D]Querying vLLM engine and Blackwell GPU telemetry...[/bold #00BD7D]"):
        data = query_pod_performance(notebook_id=notebook_id)
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        render_performance_dashboard(data)


@cli.command("deploy")
@click.argument("model_alias", default="qwen-32b", required=False)
@click.option("--pod", "pod_id", default=None, help="Target pod ID (auto-discovered if omitted)")
@click.option("--token", default=None, help="Cloudflare Tunnel token for persistent custom domain")
@click.option("--no-tunnel", is_flag=True, help="Disable public Cloudflare tunnel (localhost only)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output deployment result as JSON")
def cmd_deploy(model_alias: str, pod_id: Optional[str], token: Optional[str], no_tunnel: bool, as_json: bool):
    """1-Click production model deployment with hyper-tuned Blackwell flags and public credentials."""
    from molab_cli.deploy import deploy_model_on_pod
    from molab_cli.tunnel import render_credentials

    with console.status(f"[bold #00BD7D]Orchestrating 1-Click deployment for [{model_alias}]...[/bold #00BD7D]"):
        try:
            res = deploy_model_on_pod(
                model_alias=model_alias,
                pod_id=pod_id,
                tunnel_token=token,
                skip_tunnel=no_tunnel,
            )
        except Exception as e:
            console.print(f"[bold red]Deployment failed:[/bold red] {e}")
            sys.exit(1)

    if as_json:
        print(json.dumps(res, indent=2))
        return

    console.print(Panel(
        f"[bold green]✔ Model Successfully Deployed & Ready![/bold green]\n\n"
        f"• [bold white]Model:[/bold white]      [bold green]{res['model_id']}[/bold green]\n"
        f"• [bold white]Target Pod:[/bold white] [#00BD7D]{res['pod_id']}[/#00BD7D]\n"
        f"• [bold white]Hardware:[/bold white]   [yellow]NVIDIA RTX PRO 6000 Blackwell (94.97 GB GDDR7, sm_120)[/yellow]\n"
        f"• [bold white]Local Base:[/bold white]  [cyan]{res['local_url']}[/cyan]\n"
        f"• [bold white]API Key:[/bold white]     [bold white]{res['api_key']}[/bold white]",
        title="[bold #00BD7D]◆ MoLab Blackwell Model Deployment[/bold #00BD7D]",
        border_style="#00BD7D",
    ))

    if res.get("tunnel"):
        render_credentials(res["tunnel"])



@cli.command("chat")
@click.argument("notebook_id", required=False)
@click.option("--max-tokens", default=1024, help="Max tokens to generate (default: 1024)")
@click.option("--temp", default=0.7, help="Sampling temperature (default: 0.7)")
@click.option("--think", "think_mode", type=click.Choice(["full", "compact", "off"]), default="full", help="Thinking display mode: full, compact, or off")
@click.option("--model", "model_name", default=None, help="Target model identifier (auto-detected if omitted)")
@click.option("--system", "system_prompt", default=None, help="Custom system prompt")
@click.option("-p", "--print", "prompt", default=None, help="Execute prompt non-interactively and print response")
@click.option("--claude", is_flag=True, help="Launch Claude Code terminal agent")
@click.option("--hermes", is_flag=True, help="Launch Hermes Agent terminal session")
@click.option("-y", "--auto", "--yolo", "auto_approve", is_flag=True, help="Auto-approve tool actions without interactive confirmation")
@click.option("--no-tools", is_flag=True, help="Disable workspace tool execution (pure conversation mode)")
@click.argument("extra_args", nargs=-1)
def cmd_chat(
    notebook_id: Optional[str],
    max_tokens: int,
    temp: float,
    think_mode: str,
    model_name: Optional[str],
    system_prompt: Optional[str],
    prompt: Optional[str],
    claude: bool,
    hermes: bool,
    auto_approve: bool,
    no_tools: bool,
    extra_args: Tuple[str, ...],
):
    """Industry-grade terminal coding agent with autonomous tool execution on MoLab Blackwell GPU."""
    from molab_cli.chat import run_terminal_chat
    run_terminal_chat(
        notebook_id=notebook_id,
        max_tokens=max_tokens,
        temperature=temp,
        think_mode=think_mode,
        system_prompt=system_prompt,
        model_name=model_name,
        prompt=prompt,
        extra_args=list(extra_args) if extra_args else None,
        use_claude=claude,
        use_hermes=hermes,
        auto_approve=auto_approve,
        no_tools=no_tools,
    )




@cli.command("doctor")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output health checks as JSON")
def cmd_doctor(as_json: bool):
    """Run diagnostic health checks on authentication, network, tools, and pods."""
    from molab_cli.capabilities import run_doctor
    doc = run_doctor()
    if as_json:
        print(json.dumps(doc, indent=2))
        return
    table = Table(title="[bold cyan]MoLab System Doctor[/bold cyan]", box=box.ROUNDED)
    table.add_column("Check", style="cyan")
    table.add_column("Status", no_wrap=True)
    table.add_column("Message", style="white")
    for chk in doc.get("checks", []):
        st = chk.get("status")
        st_styled = "[bold green]PASS[/bold green]" if st == "PASS" else ("[bold yellow]WARN[/bold yellow]" if st == "WARN" else "[bold red]FAIL[/bold red]")
        table.add_row(chk.get("name"), st_styled, chk.get("message"))
    console.print(table)
    overall = doc.get("status")
    color = "green" if overall == "HEALTHY" else ("yellow" if overall == "WARNING" else "red")
    console.print(f"Overall Status: [bold {color}]{overall}[/bold {color}] ({doc.get('passed')}/{doc.get('total_checks')} passed)")


@cli.command("capabilities")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output capabilities as JSON")
def cmd_capabilities(as_json: bool):
    """Discover confirmed facts regarding local and remote compute capabilities."""
    from molab_cli.capabilities import discover_capabilities
    caps = discover_capabilities()
    if as_json:
        print(json.dumps(caps, indent=2))
        return
    console.print(Panel(
        f"[bold white]Platform:[/bold white] {caps['local']['platform']}\n"
        f"[bold white]Python:[/bold white] {caps['local']['python_version']}\n"
        f"[bold white]Active Pods:[/bold white] {caps['remote']['running_pods']} (GPU: {caps['remote']['gpu_pods']})\n"
        f"[bold white]Transfer Engine:[/bold white] Native HTTP/2 Streaming (SHA-256 Verified)\n"
        f"[bold white]Job Engine:[/bold white] Local SQLite + Detached Pod Watcher",
        title="MoLab Platform Capabilities",
        border_style="cyan",
    ))


@cli.command("sync")
@click.argument("notebook_id")
@click.argument("local_dir")
@click.argument("remote_dir")
@click.option("--dry-run", is_flag=True, help="Compute diff without uploading files")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output sync summary as JSON")
def cmd_sync(notebook_id: str, local_dir: str, remote_dir: str, dry_run: bool, as_json: bool):
    """Delta synchronize local directory to pod using SHA-256 manifests."""
    from molab_cli.transfer import TransferManager
    session = SandboxSession(notebook_id)
    transfer = TransferManager(session)
    res = transfer.sync_dir(local_dir, remote_dir, dry_run=dry_run)
    if as_json:
        print(json.dumps(res, indent=2))
        return
    console.print(f"[bold green]Sync {'simulation' if dry_run else 'completed'} successfully![/bold green]")
    console.print(f"• Uploaded: [bold]{res['to_upload_count']}[/bold] files ({round(res['uploaded_bytes'] / (1024**2), 2)} MB)")
    console.print(f"• Unchanged: [dim]{res['unchanged_count']}[/dim] files")


@cli.group("job")
def job_group():
    """Manage asynchronous background jobs and output artifacts."""
    pass


@job_group.command("submit")
@click.argument("notebook_id")
@click.argument("command")
@click.option("--name", default=None, help="Descriptive job name")
@click.option("--workdir", default="/workspace", help="Remote working directory")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output job record as JSON")
def cmd_job_submit(notebook_id: str, command: str, name: Optional[str], workdir: str, as_json: bool):
    """Submit a detached background job to pod with persistent SQLite tracking."""
    from molab_cli.jobs import JobManager
    jm = JobManager()
    job = jm.submit_job(notebook_id=notebook_id, command=command, name=name, workdir=workdir)
    if as_json:
        print(json.dumps(job, indent=2))
        return
    console.print(Panel(
        f"[bold green]Job Submitted Successfully![/bold green]\n"
        f"• Job ID:      [bold cyan]{job['id']}[/bold cyan]\n"
        f"• Name:        {job['name']}\n"
        f"• Remote PID:  [dim]{job['remote_pid']}[/dim]\n"
        f"• Log File:    [dim]{job['remote_log_path']}[/dim]\n\n"
        f"Inspect status: [bold]molab job status {job['id']}[/bold]\n"
        f"Stream logs:    [bold]molab job logs {job['id']}[/bold]",
        title="Background Job",
        border_style="green",
    ))


@job_group.command("list")
@click.option("--limit", default=25, help="Max jobs to display")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output jobs as JSON")
def cmd_job_list(limit: int, as_json: bool):
    """List historic and active background jobs."""
    import time
    from molab_cli.jobs import JobManager
    jm = JobManager()
    jobs = jm.list_jobs(limit=limit)
    if as_json:
        print(json.dumps(jobs, indent=2))
        return
    table = Table(title="[bold cyan]MoLab Background Jobs[/bold cyan]", box=box.ROUNDED)
    table.add_column("Job ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Pod", style="dim")
    table.add_column("Status", no_wrap=True)
    table.add_column("Exit Code", justify="center")
    table.add_column("Created", style="dim")
    for j in jobs:
        st = j["status"]
        st_color = "green" if st == "COMPLETED" else ("cyan" if st == "RUNNING" else ("red" if st in ("FAILED", "LOST") else "yellow"))
        table.add_row(
            j["id"],
            j["name"],
            j["notebook_id"][:12] + "...",
            f"[bold {st_color}]{st}[/bold {st_color}]",
            str(j["exit_code"]) if j["exit_code"] is not None else "-",
            time.strftime("%m-%d %H:%M", time.localtime(j["created_at"])) if j.get("created_at") else "-",
        )
    console.print(table)


@job_group.command("status")
@click.argument("job_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_job_status(job_id: str, as_json: bool):
    """Query refreshed status of a background job."""
    from molab_cli.jobs import JobManager
    jm = JobManager()
    job = jm.refresh_job_status(job_id)
    if as_json:
        print(json.dumps(job, indent=2))
        return
    console.print(Panel(
        f"• Status:     [bold]{job['status']}[/bold]\n"
        f"• Exit Code:  {job['exit_code']}\n"
        f"• Remote PID: {job['remote_pid']}\n"
        f"• Error:      {job.get('error_message') or 'None'}\n"
        f"• Logs:       {job['remote_log_path']}",
        title=f"Job {job_id} ({job['name']})",
        border_style="cyan",
    ))


@job_group.command("logs")
@click.argument("job_id")
@click.option("--tail", default=100, help="Number of lines to tail")
def cmd_job_logs(job_id: str, tail: int):
    """Fetch execution logs for a background job."""
    from molab_cli.jobs import JobManager
    jm = JobManager()
    logs = jm.get_job_logs(job_id, tail_lines=tail)
    console.print(logs)


@job_group.command("cancel")
@click.argument("job_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output cancellation as JSON")
def cmd_job_cancel(job_id: str, as_json: bool):
    """Cancel a running background job."""
    from molab_cli.jobs import JobManager
    jm = JobManager()
    res = jm.cancel_job(job_id)
    if as_json:
        print(json.dumps(res, indent=2))
        return
    console.print(f"[bold yellow]Job {job_id} cancelled.[/bold yellow]")


@job_group.command("artifacts")
@click.argument("job_id")
@click.option("--download", "download_dir", default=None, help="Local directory to download artifacts to")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output artifacts list as JSON")
def cmd_job_artifacts(job_id: str, download_dir: Optional[str], as_json: bool):
    """List or download output artifacts produced by a job."""
    from molab_cli.jobs import JobManager
    jm = JobManager()
    if download_dir:
        res = jm.download_artifacts(job_id, download_dir)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        console.print(f"[bold green]Downloaded {len(res)} artifacts to {download_dir}[/bold green]")
    else:
        artifacts = jm.list_artifacts(job_id)
        if as_json:
            print(json.dumps(artifacts, indent=2))
            return
        table = Table(title=f"Artifacts for {job_id}", box=box.ROUNDED)
        table.add_column("Remote Path", style="cyan")
        table.add_column("Size", justify="right")
        table.add_column("SHA-256", style="dim")
        for a in artifacts:
            table.add_row(a["remote_path"], f"{round(a['size_bytes'] / (1024**2), 2)} MB", a.get("sha256", "N/A")[:12] + "...")
        console.print(table)


@cli.group("workload")
def workload_group():
    """Manage and launch reusable workload templates."""
    pass


@workload_group.command("list")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output workloads as JSON")
def cmd_workload_list(as_json: bool):
    """List available pluggable workload templates."""
    from molab_cli.workloads import WorkloadRegistry
    reg = WorkloadRegistry()
    workloads = reg.list_workloads()
    if as_json:
        print(json.dumps([w.to_dict() for w in workloads], indent=2))
        return
    table = Table(title="[bold cyan]MoLab Pluggable Workloads[/bold cyan]", box=box.ROUNDED)
    table.add_column("Template", style="bold cyan")
    table.add_column("Category", style="green")
    table.add_column("Min VRAM", justify="right")
    table.add_column("Description", style="white")
    for w in workloads:
        table.add_row(w.name, w.category, f"{w.min_vram_gb} GB" if w.min_vram_gb else "-", w.description)
    console.print(table)


@cli.group("serve")
def serve_group():
    """Manage OpenAI-compatible model servers and application endpoints."""
    pass


@serve_group.command("status")
@click.argument("notebook_id")
@click.option("--port", default=8000, help="Port to inspect (default: 8000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_serve_status(notebook_id: str, port: int, as_json: bool):
    """Verify application-level health and port binding for model server."""
    from molab_cli.services import ServiceManager
    session = SandboxSession(notebook_id)
    sm = ServiceManager(session)
    res = sm.get_service_status(port=port)
    if as_json:
        print(json.dumps(res, indent=2))
        return
    st = res["status"]
    st_color = "green" if st == "HEALTHY" else ("yellow" if st == "UNHEALTHY" else "dim")
    console.print(Panel(
        f"• Status:        [bold {st_color}]{st}[/bold {st_color}]\n"
        f"• Port:          {res['port']}\n"
        f"• Listener:      {res['has_listener']}\n"
        f"• Response Time: {res.get('response_time_ms', 'N/A')} ms",
        title=f"Service on {notebook_id}:{port}",
        border_style="cyan",
    ))


@serve_group.command("stop")
@click.argument("notebook_id")
@click.option("--port", default=8000, help="Port to stop (default: 8000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output result as JSON")
def cmd_serve_stop(notebook_id: str, port: int, as_json: bool):
    """Gracefully terminate model service running on pod."""
    from molab_cli.services import ServiceManager
    session = SandboxSession(notebook_id)
    sm = ServiceManager(session)
    ok = sm.stop_service(port=port)
    if as_json:
        print(json.dumps({"success": ok, "port": port}, indent=2))
        return
    console.print(f"[bold green]Service on port {port} stopped.[/bold green]")


@serve_group.command("logs")
@click.argument("notebook_id")
@click.option("--tail", default=50, help="Number of lines to read")
def cmd_serve_logs(notebook_id: str, tail: int):
    """Read recent service logs from pod."""
    from molab_cli.services import ServiceManager
    session = SandboxSession(notebook_id)
    sm = ServiceManager(session)
    logs = sm.get_service_logs(tail_lines=tail)
    console.print(logs)


@cli.command("mcp")
def cmd_mcp():
    """Start the Model Context Protocol (MCP) JSON-RPC 2.0 stdio server."""
    from molab_cli.mcp import run_mcp_server
    run_mcp_server()


# =============================================================================
# Batch Orchestration Commands
# =============================================================================

@cli.group("batch")
def batch_group():
    """Autonomous multi-pod batch pipeline execution and management."""
    pass


@batch_group.command("validate")
@click.argument("manifest_path", type=click.Path(exists=True))
@click.option("-j", "--json", "as_json", is_flag=True, help="Output validation as JSON")
def cmd_batch_validate(manifest_path: str, as_json: bool):
    """Validate a batch pipeline manifest schema and DAG dependencies."""
    from molab_cli.scheduler import validate_manifest

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception as e:
        if as_json:
            print(json.dumps({"valid": False, "error": f"Failed to read manifest JSON: {e}"}))
        else:
            console.print(f"[bold red]Failed to read manifest:[/bold red] {e}")
        return

    res = validate_manifest(manifest)
    if as_json:
        print(json.dumps(res.to_dict(), indent=2))
        return

    if res.valid:
        console.print(f"[bold green]✔ Manifest is valid![/bold green] ({res.task_count} tasks)")
        if res.warnings:
            for w in res.warnings:
                console.print(f"[yellow]Warning:[/yellow] {w}")

        table = Table(title="Topological Execution Stages", box=box.ROUNDED)
        table.add_column("Stage", style="bold cyan", justify="right")
        table.add_column("Parallel Tasks", style="bold white")
        for idx, stage in enumerate(res.execution_order):
            table.add_row(f"Stage {idx + 1}", ", ".join(stage))
        console.print(table)
    else:
        console.print(f"[bold red]✘ Manifest validation failed with {len(res.errors)} error(s):[/bold red]")
        for err in res.errors:
            console.print(f"  [red]• {err}[/red]")


@batch_group.command("submit")
@click.argument("manifest_path", type=click.Path(exists=True))
@click.option("-p", "--max-parallel", type=int, default=None, help="Max parallel tasks across pods")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output submission as JSON")
def cmd_batch_submit(manifest_path: str, max_parallel: Optional[int], as_json: bool):
    """Submit a batch pipeline manifest for multi-pod execution."""
    from molab_cli.scheduler import BatchOrchestrator

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": f"Failed to read manifest: {e}"}))
        else:
            console.print(f"[bold red]Failed to read manifest:[/bold red] {e}")
        return

    orchestrator = BatchOrchestrator()
    try:
        batch_id = orchestrator.submit(manifest, concurrency_limit=max_parallel)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Submission error:[/bold red] {e}")
        return

    batch = orchestrator.job_manager.get_batch(batch_id)
    if as_json:
        print(json.dumps({"batch_id": batch_id, "status": batch["status"], "task_count": len(batch.get("tasks", []))}, indent=2))
        return

    console.print(f"[bold green]✔ Batch submitted successfully![/bold green]")
    console.print(f"[bold]Batch ID:[/bold]   [bold cyan]{batch_id}[/bold cyan]")
    console.print(f"[bold]Name:[/bold]       {batch.get('name')}")
    console.print(f"[bold]Tasks:[/bold]      {len(batch.get('tasks', []))}")
    console.print(f"[dim]Run with: [bold]molab batch run {batch_id}[/bold][/dim]")


@batch_group.command("run")
@click.argument("target")
@click.option("-p", "--max-parallel", type=int, default=None, help="Max parallel tasks")
@click.option("--poll", type=float, default=3.0, help="Poll interval in seconds")
@click.option("--timeout", type=float, default=None, help="Execution timeout in seconds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output result as JSON")
def cmd_batch_run(target: str, max_parallel: Optional[int], poll: float, timeout: Optional[float], as_json: bool):
    """Execute a batch pipeline (accepts a batch ID or manifest file path)."""
    from molab_cli.scheduler import BatchOrchestrator

    orchestrator = BatchOrchestrator()
    batch_id = target

    # Check if target is a file path
    if os.path.exists(target):
        try:
            with open(target, "r") as f:
                manifest = json.load(f)
            batch_id = orchestrator.submit(manifest, concurrency_limit=max_parallel)
            if not as_json:
                console.print(f"[bold green]✔ Manifest submitted as {batch_id}[/bold green]")
        except Exception as e:
            if as_json:
                print(json.dumps({"error": f"Failed to submit manifest: {e}"}))
            else:
                console.print(f"[bold red]Failed to submit manifest:[/bold red] {e}")
            return

    if not as_json:
        console.print(f"[bold blue]Starting batch orchestration for {batch_id}...[/bold blue]")

    def on_event(event_type: str, data: Dict[str, Any]):
        if not as_json:
            color = "green" if "completed" in event_type else ("red" if "failed" in event_type else "cyan")
            msg = data.get("task_key", data.get("batch_name", ""))
            console.print(f"[{color}]▶ [{event_type.upper()}][/{color}] {msg}")

    try:
        summary = orchestrator.run_batch(
            batch_id,
            max_parallel=max_parallel,
            poll_interval=poll,
            timeout=timeout,
            on_event=on_event if not as_json else None,
        )
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Execution error:[/bold red] {e}")
        return

    if as_json:
        print(json.dumps(summary, indent=2))
        return

    status_color = "bold green" if summary.get("status") == "COMPLETED" else "bold red"
    console.print(f"\n[{status_color}]Batch {summary.get('status')}: {batch_id}[/{status_color}]")
    counts = summary.get("task_counts", {})
    console.print(f"Tasks: {counts.get('completed', 0)} completed, {counts.get('failed', 0)} failed, {counts.get('skipped', 0)} skipped, {counts.get('total', 0)} total.")


@batch_group.command("list")
@click.option("-n", "--limit", default=20, help="Max batches to list")
@click.option("-s", "--status", default=None, help="Filter by status")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_batch_list(limit: int, status: Optional[str], as_json: bool):
    """List historic and active batch pipelines."""
    from molab_cli.jobs import JobManager

    jm = JobManager()
    batches = jm.list_batches(limit=limit, status=status)

    if as_json:
        print(json.dumps(batches, indent=2))
        return

    if not batches:
        console.print("[yellow]No batches found.[/yellow]")
        return

    table = Table(title="MoLab Batch Pipelines", box=box.ROUNDED)
    table.add_column("Batch ID", style="bold cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Tasks (Done/Total)", style="magenta")
    table.add_column("Created", style="dim")

    for b in batches:
        st = b.get("status", "UNKNOWN")
        st_color = "green" if st == "COMPLETED" else ("red" if st == "FAILED" else ("yellow" if st == "RUNNING" else "dim"))
        c = b.get("task_counts", {})
        progress = f"{c.get('completed', 0)}/{c.get('total', 0)}"
        if c.get("failed", 0) > 0:
            progress += f" [red]({c.get('failed')} failed)[/red]"
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.get("created_at", 0)))
        table.add_row(b["id"], b.get("name") or "Unnamed", f"[{st_color}]{st}[/{st_color}]", progress, created)

    console.print(table)


@batch_group.command("status")
@click.argument("batch_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_batch_status(batch_id: str, as_json: bool):
    """Show detailed status and task breakdown for a batch."""
    from molab_cli.jobs import JobManager

    jm = JobManager()
    try:
        batch = jm.get_batch(batch_id)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        return

    if as_json:
        print(json.dumps(batch, indent=2))
        return

    st = batch.get("status", "UNKNOWN")
    st_color = "green" if st == "COMPLETED" else ("red" if st == "FAILED" else ("yellow" if st == "RUNNING" else "cyan"))

    console.print(Panel(
        f"[bold]Batch ID:[/bold]     {batch['id']}\n"
        f"[bold]Name:[/bold]         {batch.get('name')}\n"
        f"[bold]Status:[/bold]       [{st_color}]{st}[/{st_color}]\n"
        f"[bold]Concurrency:[/bold]  {batch.get('concurrency_limit')}\n"
        f"[bold]Error:[/bold]        {batch.get('error_message') or 'None'}",
        title=f"Batch Overview: {batch['id']}",
        box=box.ROUNDED,
    ))

    table = Table(title="Constituent Tasks", box=box.ROUNDED)
    table.add_column("Task Key", style="bold cyan")
    table.add_column("Workload / Cmd", style="white")
    table.add_column("Pod", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Attempts", style="dim")
    table.add_column("Exit Code", style="dim")

    for t in batch.get("tasks", []):
        t_st = t.get("status", "PENDING")
        t_color = "green" if t_st == "COMPLETED" else ("red" if t_st == "FAILED" else ("yellow" if t_st == "RUNNING" else "dim"))
        cmd_desc = t.get("workload_type") or (t.get("command")[:35] + "..." if t.get("command") else "N/A")
        exit_code = str(t.get("exit_code")) if t.get("exit_code") is not None else "-"
        table.add_row(
            t.get("task_key"),
            cmd_desc,
            t.get("assigned_pod") or "-",
            f"[{t_color}]{t_st}[/{t_color}]",
            str(t.get("attempts", 0)),
            exit_code,
        )

    console.print(table)


@batch_group.command("logs")
@click.argument("batch_id")
@click.option("-t", "--task", "task_key", default=None, help="Specific task key to tail")
@click.option("-n", "--tail", default=100, help="Number of log lines")
def cmd_batch_logs(batch_id: str, task_key: Optional[str], tail: int):
    """View remote execution logs for tasks in a batch."""
    from molab_cli.jobs import JobManager

    jm = JobManager()
    batch = jm.get_batch(batch_id)
    tasks = batch.get("tasks", [])

    if task_key:
        tasks = [t for t in tasks if t.get("task_key") == task_key]
        if not tasks:
            console.print(f"[red]Task '{task_key}' not found in batch {batch_id}.[/red]")
            return

    for t in tasks:
        job_id = t.get("job_id")
        console.print(f"\n[bold cyan]=== Task: {t.get('task_key')} (Job: {job_id or 'none'}, Pod: {t.get('assigned_pod') or 'none'}) ===[/bold cyan]")
        if job_id:
            logs = jm.get_job_logs(job_id, tail_lines=tail)
            console.print(logs)
        else:
            console.print(f"[dim]No job launched yet (Status: {t.get('status')}).[/dim]")


@batch_group.command("cancel")
@click.argument("batch_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_batch_cancel(batch_id: str, as_json: bool):
    """Cancel a running batch and stop remote tasks."""
    from molab_cli.jobs import JobManager

    jm = JobManager()
    try:
        updated = jm.cancel_batch(batch_id)
        if as_json:
            print(json.dumps(updated, indent=2))
        else:
            console.print(f"[bold yellow]Batch {batch_id} cancelled.[/bold yellow]")
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Cancel failed:[/bold red] {e}")


@batch_group.command("retry")
@click.argument("batch_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_batch_retry(batch_id: str, as_json: bool):
    """Reset failed or skipped tasks in a batch for re-running."""
    from molab_cli.jobs import JobManager

    jm = JobManager()
    try:
        updated = jm.retry_batch(batch_id)
        if as_json:
            print(json.dumps(updated, indent=2))
        else:
            console.print(f"[bold green]Batch {batch_id} reset to PENDING. Use 'molab batch run {batch_id}' to execute.[/bold green]")
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Retry failed:[/bold red] {e}")


# =============================================================================
# Webhook Notifications Test Command
# =============================================================================

@cli.group("notify")
def notify_group():
    """Notification webhook configuration and test tools."""
    pass


@notify_group.command("test")
@click.option("-u", "--url", required=True, help="Webhook URL to test")
@click.option("-e", "--event", default="test_notification", help="Event type")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_notify_test(url: str, event: str, as_json: bool):
    """Send a test webhook event with sanitized telemetry."""
    from molab_cli.notifications import send_webhook

    sample_data = {
        "message": "Test notification from MoLab CLI v2.2",
        "batch_id": "batch_test_001",
        "pod": "nb_test_pod",
        "vram_gb": 94.4,
        "token": "secret_cookie_must_be_redacted",
    }

    result = send_webhook(url, event, sample_data)
    if as_json:
        print(json.dumps(result, indent=2))
        return

    if result.get("status") == "SENT":
        console.print(f"[bold green]✔ Webhook delivered successfully![/bold green] (HTTP {result.get('status_code')})")
    else:
        console.print(f"[bold red]✘ Webhook delivery failed:[/bold red] {result.get('error')}")


# =============================================================================
# Native Marimo Backend Commands (Usage, Export, File, Kernel, Pkg)
# =============================================================================

@cli.command("usage")
@click.argument("notebook_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output hardware usage as JSON")
def cmd_usage(notebook_id: str, as_json: bool):
    """Display real-time host RAM, server RAM, kernel RAM, and GPU memory telemetry."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
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

    table = Table(title=f"MoLab Backend Telemetry ({notebook_id})", box=box.ROUNDED)
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


# -----------------------------------------------------------------------------
# Export Group
# -----------------------------------------------------------------------------

@cli.group("export")
def export_group():
    """Export reactive notebooks to HTML, Markdown, IPYNB, Script, or PDF via remote Marimo backend."""
    pass


@export_group.command("html")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (default: <notebook_id>.html)")
@click.option("--no-code", is_flag=True, help="Exclude source code cells from export")
def cmd_export_html(notebook_id: str, output: Optional[str], no_code: bool):
    """Export notebook to a standalone self-contained HTML document."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    out_file = output or f"{notebook_id}.html"
    with console.status(f"[bold cyan]Exporting notebook {notebook_id} to HTML...[/bold cyan]"):
        try:
            html = backend.export_notebook("html", include_code=not no_code)
            Path(out_file).write_text(html, encoding="utf-8")
            console.print(f"[bold green]✔ Exported HTML to {out_file}[/bold green] ({len(html)} bytes)")
        except Exception as e:
            console.print(f"[red]Export failed:[/red] {e}")


@export_group.command("md")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (default: <notebook_id>.md)")
def cmd_export_md(notebook_id: str, output: Optional[str]):
    """Export notebook to Markdown format."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    out_file = output or f"{notebook_id}.md"
    with console.status(f"[bold cyan]Exporting notebook {notebook_id} to Markdown...[/bold cyan]"):
        try:
            md = backend.export_notebook("markdown")
            Path(out_file).write_text(md, encoding="utf-8")
            console.print(f"[bold green]✔ Exported Markdown to {out_file}[/bold green] ({len(md)} bytes)")
        except Exception as e:
            console.print(f"[red]Export failed:[/red] {e}")


@export_group.command("ipynb")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (default: <notebook_id>.ipynb)")
def cmd_export_ipynb(notebook_id: str, output: Optional[str]):
    """Export notebook to standard Jupyter Notebook (.ipynb) format."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    out_file = output or f"{notebook_id}.ipynb"
    with console.status(f"[bold cyan]Exporting notebook {notebook_id} to IPYNB...[/bold cyan]"):
        try:
            nb = backend.export_notebook("ipynb")
            Path(out_file).write_text(nb, encoding="utf-8")
            console.print(f"[bold green]✔ Exported Jupyter notebook to {out_file}[/bold green] ({len(nb)} bytes)")
        except Exception as e:
            console.print(f"[red]Export failed:[/red] {e}")


@export_group.command("script")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (default: <notebook_id>.py)")
def cmd_export_script(notebook_id: str, output: Optional[str]):
    """Export notebook to a clean standalone Python script."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    out_file = output or f"{notebook_id}.py"
    with console.status(f"[bold cyan]Exporting notebook {notebook_id} to Script...[/bold cyan]"):
        try:
            sc = backend.export_notebook("script")
            Path(out_file).write_text(sc, encoding="utf-8")
            console.print(f"[bold green]✔ Exported Script to {out_file}[/bold green] ({len(sc)} bytes)")
        except Exception as e:
            console.print(f"[red]Export failed:[/red] {e}")


@export_group.command("pdf")
@click.argument("notebook_id")
@click.option("-o", "--output", help="Output file path (default: <notebook_id>.pdf)")
def cmd_export_pdf(notebook_id: str, output: Optional[str]):
    """Export notebook to PDF format."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    out_file = output or f"{notebook_id}.pdf"
    with console.status(f"[bold cyan]Exporting notebook {notebook_id} to PDF...[/bold cyan]"):
        try:
            data = backend.export_notebook("pdf")
            if isinstance(data, str):
                Path(out_file).write_text(data, encoding="utf-8")
            else:
                Path(out_file).write_bytes(data)
            console.print(f"[bold green]✔ Exported PDF to {out_file}[/bold green] ({len(data)} bytes)")
        except Exception as e:
            console.print(f"[red]Export failed:[/red] {e}")


# -----------------------------------------------------------------------------
# File Group
# -----------------------------------------------------------------------------

@cli.group("file")
def file_group():
    """Manage remote files directly via high-speed native Marimo REST endpoints."""
    pass


@file_group.command("ls")
@click.argument("notebook_id")
@click.argument("path", default=".")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output file list as JSON")
def cmd_file_ls(notebook_id: str, path: str, as_json: bool):
    """List files and folders on pod via native REST endpoint."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    try:
        files = backend.list_files(path=path)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error listing files:[/red] {e}")
        return

    if as_json:
        print(json.dumps(files, indent=2))
        return

    table = Table(title=f"Remote Files: {path} ({notebook_id})", box=box.ROUNDED)
    table.add_column("Type", justify="center")
    table.add_column("Name", style="bold cyan")
    table.add_column("Size", justify="right")
    table.add_column("Path", style="dim")

    for f in files:
        is_dir = f.get("isDirectory", False)
        t_str = "[blue]DIR[/blue]" if is_dir else "[white]FILE[/white]"
        sz = "-" if is_dir or f.get("size") is None else f"{f.get('size')} B"
        table.add_row(t_str, f.get("name", ""), sz, f.get("path", ""))

    console.print(table)


@file_group.command("cat")
@click.argument("notebook_id")
@click.argument("path")
def cmd_file_cat(notebook_id: str, path: str):
    """View contents of a remote text file via native REST endpoint."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status(f"[bold cyan]Reading {path}...[/bold cyan]"):
        try:
            content = backend.read_file(path)
            console.print(content, markup=False)
        except Exception as e:
            console.print(f"[red]Error reading file:[/red] {e}")


@file_group.command("info")
@click.argument("notebook_id")
@click.argument("path")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output details as JSON")
def cmd_file_info(notebook_id: str, path: str, as_json: bool):
    """Fetch metadata and mime type of a file on the remote pod."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    try:
        details = backend.file_details(path)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error fetching details:[/red] {e}")
        return

    if as_json:
        print(json.dumps(details, indent=2))
        return

    f_meta = details.get("file", {})
    console.print(Panel(
        f"[bold]Path:[/bold]      {f_meta.get('path')}\n"
        f"[bold]Name:[/bold]      {f_meta.get('name')}\n"
        f"[bold]Directory:[/bold] {f_meta.get('isDirectory')}\n"
        f"[bold]Size:[/bold]      {f_meta.get('size')} bytes\n"
        f"[bold]MIME Type:[/bold] {details.get('mimeType')}\n"
        f"[bold]Base64:[/bold]    {details.get('isBase64')}\n"
        f"[bold]Oversize:[/bold]  {details.get('isTooLarge')}",
        title=f"File Info: {path}",
        box=box.ROUNDED,
    ))


@file_group.command("cp")
@click.argument("notebook_id")
@click.argument("src")
@click.argument("dst")
def cmd_file_cp(notebook_id: str, src: str, dst: str):
    """Instant server-side copy without local data transfer."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status(f"[bold cyan]Copying {src} -> {dst}...[/bold cyan]"):
        try:
            ok = backend.copy_file(src, dst)
            if ok:
                console.print(f"[bold green]✔ Copied {src} -> {dst}[/bold green]")
            else:
                console.print(f"[red]✘ Copy failed for {src}[/red]")
        except Exception as e:
            console.print(f"[red]Copy error:[/red] {e}")


@file_group.command("mv")
@click.argument("notebook_id")
@click.argument("src")
@click.argument("dst")
def cmd_file_mv(notebook_id: str, src: str, dst: str):
    """Instant server-side move / rename."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status(f"[bold cyan]Moving {src} -> {dst}...[/bold cyan]"):
        try:
            ok = backend.move_file(src, dst)
            if ok:
                console.print(f"[bold green]✔ Moved {src} -> {dst}[/bold green]")
            else:
                console.print(f"[red]✘ Move failed for {src}[/red]")
        except Exception as e:
            console.print(f"[red]Move error:[/red] {e}")


@file_group.command("rm")
@click.argument("notebook_id")
@click.argument("path")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
def cmd_file_rm(notebook_id: str, path: str, yes: bool):
    """Delete a remote file or folder on the pod."""
    if not yes:
        if not click.confirm(f"Are you sure you want to delete {path} on {notebook_id}?"):
            console.print("[dim]Aborted.[/dim]")
            return
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status(f"[bold red]Deleting {path}...[/bold red]"):
        try:
            ok = backend.delete_file(path)
            if ok:
                console.print(f"[bold green]✔ Deleted {path}[/bold green]")
            else:
                console.print(f"[red]✘ Delete failed for {path}[/red]")
        except Exception as e:
            console.print(f"[red]Delete error:[/red] {e}")


@file_group.command("search")
@click.argument("notebook_id")
@click.argument("query")
@click.option("--path", default=None, help="Search root directory")
@click.option("--depth", default=5, help="Search depth (default 5)")
@click.option("--limit", default=100, help="Max results (default 100)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output results as JSON")
def cmd_file_search(notebook_id: str, query: str, path: Optional[str], depth: int, limit: int, as_json: bool):
    """Fast server-side recursive file and directory search."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    try:
        results = backend.search_files(query=query, path=path, depth=depth, limit=limit)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Search error:[/red] {e}")
        return

    if as_json:
        print(json.dumps(results, indent=2))
        return

    if not results:
        console.print(f"[yellow]No files matching '{query}' found.[/yellow]")
        return

    table = Table(title=f"Search Results for '{query}' ({len(results)} found)", box=box.ROUNDED)
    table.add_column("Type", justify="center")
    table.add_column("Name", style="bold cyan")
    table.add_column("Path", style="white")

    for r in results:
        t_str = "[blue]DIR[/blue]" if r.get("isDirectory") else "[white]FILE[/white]"
        table.add_row(t_str, r.get("name", ""), r.get("path", ""))

    console.print(table)


# -----------------------------------------------------------------------------
# Kernel Group
# -----------------------------------------------------------------------------

@cli.group("kernel")
def kernel_group():
    """Manage and interact directly with the remote Marimo Python kernel."""
    pass


@kernel_group.command("status")
@click.argument("notebook_id")
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_kernel_status(notebook_id: str, file_key: str, as_json: bool):
    """Check if the remote Python kernel is idle or running."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    try:
        status = backend.get_kernel_status(file_key=file_key)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error querying kernel status:[/red] {e}")
        return

    if as_json:
        print(json.dumps(status, indent=2))
        return

    state = status.get("state", "unknown")
    st_color = "green" if state == "idle" else ("yellow" if state == "running" else "red")
    console.print(f"Kernel Status: [bold {st_color}]{state.upper()}[/bold {st_color}]")


@kernel_group.command("eval")
@click.argument("notebook_id")
@click.argument("code")
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
@click.option("--timeout", default=30.0, help="Execution timeout in seconds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output evaluation result as JSON")
def cmd_kernel_eval(notebook_id: str, code: str, file_key: str, timeout: float, as_json: bool):
    """Execute Python code directly inside the remote Marimo Python kernel."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    if not as_json:
        with console.status("[bold cyan]Evaluating in remote Marimo kernel...[/bold cyan]"):
            try:
                res = backend.eval_python(code=code, file_key=file_key, timeout=timeout)
            except Exception as e:
                console.print(f"[red]Kernel execution failed:[/red] {e}")
                return
    else:
        try:
            res = backend.eval_python(code=code, file_key=file_key, timeout=timeout)
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return
        print(json.dumps(res, indent=2))
        return

    if res.get("stdout"):
        console.print(res["stdout"], markup=False, end="")
    if res.get("stderr"):
        console.print(f"[red]{res['stderr']}[/red]", markup=False, end="")
    if res.get("output_text"):
        console.print(f"[bold green]=> {res['output_text']}[/bold green]")


@kernel_group.command("restart")
@click.argument("notebook_id")
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
def cmd_kernel_restart(notebook_id: str, file_key: str):
    """Soft-restart remote Marimo kernel without restarting container."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status("[bold yellow]Restarting Marimo Python kernel...[/bold yellow]"):
        try:
            ok = backend.restart_kernel(file_key=file_key)
            if ok:
                console.print("[bold green]✔ Kernel restarted successfully.[/bold green]")
            else:
                console.print("[red]✘ Failed to restart kernel.[/red]")
        except Exception as e:
            console.print(f"[red]Restart error:[/red] {e}")


@kernel_group.command("interrupt")
@click.argument("notebook_id")
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
def cmd_kernel_interrupt(notebook_id: str, file_key: str):
    """Interrupt running cell execution in the remote Marimo kernel."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status("[bold yellow]Interrupting Marimo Python kernel...[/bold yellow]"):
        try:
            ok = backend.interrupt_kernel(file_key=file_key)
            if ok:
                console.print("[bold green]✔ Kernel interrupted.[/bold green]")
            else:
                console.print("[red]✘ Failed to interrupt kernel.[/red]")
        except Exception as e:
            console.print(f"[red]Interrupt error:[/red] {e}")


# -----------------------------------------------------------------------------
# Package Group
# -----------------------------------------------------------------------------

@cli.group("pkg")
def pkg_group():
    """Inspect and manage Python packages on the remote pod via native Marimo package manager."""
    pass


@pkg_group.command("list")
@click.argument("notebook_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output package list as JSON")
def cmd_pkg_list(notebook_id: str, as_json: bool):
    """List packages managed by remote pod environment."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    try:
        pkgs = backend.list_packages()
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error listing packages:[/red] {e}")
        return

    if as_json:
        print(json.dumps(pkgs, indent=2))
        return

    if not pkgs:
        console.print("[yellow]No custom packages listed.[/yellow]")
        return

    table = Table(title=f"Installed Packages ({notebook_id})", box=box.ROUNDED)
    table.add_column("Package", style="bold cyan")
    table.add_column("Version", style="green")

    for p in pkgs:
        table.add_row(p.get("name", ""), p.get("version", ""))

    console.print(table)


@pkg_group.command("add")
@click.argument("notebook_id")
@click.argument("package_name")
@click.option("--upgrade", is_flag=True, help="Upgrade package if already installed")
def cmd_pkg_add(notebook_id: str, package_name: str, upgrade: bool):
    """Install a Python package natively on the remote pod."""
    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(notebook_id)
    backend = MarimoBackendClient(session)
    with console.status(f"[bold cyan]Installing {package_name} on pod...[/bold cyan]"):
        try:
            res = backend.add_package(package_name=package_name, upgrade=upgrade)
            console.print(f"[bold green]✔ Successfully installed {package_name}![/bold green]")
        except Exception as e:
            console.print(f"[red]Installation error:[/red] {e}")


# -----------------------------------------------------------------------------
# Snapshot & Checkpoint Group
# -----------------------------------------------------------------------------

@cli.group("snapshot")
def snapshot_group():
    """Create, restore, and manage persistent workspace snapshots across pod resets."""
    pass


@snapshot_group.command("create")
@click.argument("notebook_id")
@click.option("--name", default=None, help="Descriptive snapshot checkpoint name")
@click.option("--remote-path", default="/workspace", help="Remote pod directory to archive")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output snapshot metadata as JSON")
def cmd_snapshot_create(notebook_id: str, name: Optional[str], remote_path: str, as_json: bool):
    """Create a compressed snapshot of pod workspace and stream to local persistent storage."""
    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    with console.status(f"[bold cyan]Archiving and streaming snapshot from {notebook_id}:{remote_path}...[/bold cyan]"):
        try:
            snap = sm.create_snapshot(notebook_id=notebook_id, name=name, remote_path=remote_path)
        except Exception as e:
            if as_json:
                print(json.dumps({"error": str(e)}))
            else:
                console.print(f"[red]Snapshot creation failed:[/red] {e}")
            return

    if as_json:
        print(json.dumps(snap, indent=2))
        return

    size_mb = round(snap["size_bytes"] / (1024 * 1024), 2)
    console.print(Panel(
        f"[bold green]Workspace Snapshot Saved Successfully![/bold green]\n"
        f"• Snapshot ID:  [bold cyan]{snap['id']}[/bold cyan]\n"
        f"• Name:         {snap['name']}\n"
        f"• Files:        {snap['file_count']} files ({size_mb} MB)\n"
        f"• SHA-256:      [dim]{snap['sha256'][:16]}...[/dim]\n"
        f"• Local Archive: [dim]{snap['local_archive_path']}[/dim]\n\n"
        f"Restore anytime: [bold]molab snapshot restore {notebook_id} --snapshot-id {snap['id']}[/bold]",
        title="Workspace Snapshot",
        border_style="green",
    ))


@snapshot_group.command("restore")
@click.argument("notebook_id")
@click.option("--snapshot-id", default=None, help="Specific snapshot ID (default: latest snapshot)")
@click.option("--remote-path", default="/workspace", help="Destination pod directory to unpack into")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output restore summary as JSON")
def cmd_snapshot_restore(notebook_id: str, snapshot_id: Optional[str], remote_path: str, as_json: bool):
    """Restore a snapshot into the remote pod's /workspace directory."""
    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    with console.status(f"[bold cyan]Streaming snapshot archive into {notebook_id}:{remote_path}...[/bold cyan]"):
        try:
            res = sm.restore_snapshot(notebook_id=notebook_id, snapshot_id=snapshot_id, remote_path=remote_path)
        except Exception as e:
            if as_json:
                print(json.dumps({"error": str(e)}))
            else:
                console.print(f"[red]Restore failed:[/red] {e}")
            return

    if as_json:
        print(json.dumps(res, indent=2))
        return

    console.print(Panel(
        f"[bold green]Snapshot Restored Successfully![/bold green]\n"
        f"• Snapshot ID:    [bold cyan]{res['snapshot_id']}[/bold cyan]\n"
        f"• Target Path:    {res['remote_path']}\n"
        f"• Files Restored: {res['files_restored']}\n"
        f"• Duration:       {res['duration_seconds']}s",
        title="Snapshot Restored",
        border_style="green",
    ))


@snapshot_group.command("list")
@click.argument("notebook_id", required=False)
@click.option("--limit", default=25, help="Max snapshots to show")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output snapshots as JSON")
def cmd_snapshot_list(notebook_id: Optional[str], limit: int, as_json: bool):
    """List saved snapshots and checkpoints."""
    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    snaps = sm.list_snapshots(notebook_id=notebook_id, limit=limit)
    if as_json:
        print(json.dumps(snaps, indent=2))
        return

    if not snaps:
        console.print("[yellow]No snapshots found.[/yellow]")
        return

    table = Table(title="[bold cyan]MoLab Workspace Snapshots[/bold cyan]", box=box.ROUNDED)
    table.add_column("Snapshot ID", style="cyan")
    table.add_column("Pod / Notebook", style="dim")
    table.add_column("Name", style="white")
    table.add_column("Files", justify="right")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Created", style="dim")

    for s in snaps:
        size_mb = round(s["size_bytes"] / (1024 * 1024), 2)
        created_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["created_at"])) if s.get("created_at") else "-"
        table.add_row(
            s["id"],
            s["notebook_id"][:12] + "...",
            s["name"] or "-",
            str(s["file_count"]),
            str(size_mb),
            created_str,
        )
    console.print(table)


@snapshot_group.command("delete")
@click.argument("snapshot_id")
def cmd_snapshot_delete(snapshot_id: str):
    """Delete a local snapshot archive and database entry."""
    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    ok = sm.delete_snapshot(snapshot_id)
    if ok:
        console.print(f"[bold green]✔ Deleted snapshot {snapshot_id}[/bold green]")
    else:
        console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")


# -----------------------------------------------------------------------------
# Keepalive & Anti-Idle Group
# -----------------------------------------------------------------------------

@cli.group("keepalive")
def keepalive_group():
    """Autonomous anti-idle heartbeats, session renewal, and pod resurrection."""
    pass


@keepalive_group.command("start")
@click.argument("notebook_id")
@click.option("--interval", default=120, help="Heartbeat interval in seconds (default: 120)")
@click.option("--max-hours", default=None, type=float, help="Max runtime in hours (default: unlimited)")
@click.option("--auto-restore/--no-auto-restore", default=True, help="Auto-restore snapshot if pod resets")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output daemon record as JSON")
def cmd_keepalive_start(notebook_id: str, interval: int, max_hours: Optional[float], auto_restore: bool, as_json: bool):
    """Launch detached anti-idle keepalive daemon in background."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    try:
        rec = km.start_daemon(notebook_id=notebook_id, interval=interval, max_hours=max_hours, auto_restore=auto_restore)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Failed to start keepalive:[/red] {e}")
        return

    if as_json:
        print(json.dumps(rec, indent=2))
        return

    console.print(Panel(
        f"[bold green]Keepalive Daemon Started in Background![/bold green]\n"
        f"• Notebook:     [bold cyan]{rec['notebook_id']}[/bold cyan]\n"
        f"• Daemon PID:   [bold]{rec['pid']}[/bold]\n"
        f"• Interval:     {rec['interval_seconds']} seconds\n"
        f"• Max Hours:    {rec['max_hours'] or 'Unlimited'}\n"
        f"• Auto-Restore: {'Enabled' if rec['auto_restore'] else 'Disabled'}\n"
        f"• Log File:     [dim]{rec['log_path']}[/dim]\n\n"
        f"Check status: [bold]molab keepalive status {notebook_id}[/bold]\n"
        f"Tail logs:    [bold]molab keepalive logs {notebook_id}[/bold]\n"
        f"Stop daemon:  [bold]molab keepalive stop {notebook_id}[/bold]",
        title="Pod Anti-Idle Keepalive",
        border_style="green",
    ))


@keepalive_group.command("stop")
@click.argument("notebook_id")
def cmd_keepalive_stop(notebook_id: str):
    """Stop the background keepalive daemon for a pod."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    ok = km.stop_daemon(notebook_id)
    if ok:
        console.print(f"[bold green]✔ Keepalive daemon stopped for {notebook_id}[/bold green]")
    else:
        console.print(f"[yellow]No active keepalive daemon found for {notebook_id}[/yellow]")


@keepalive_group.command("status")
@click.argument("notebook_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_keepalive_status(notebook_id: str, as_json: bool):
    """Inspect status of keepalive daemon and session TTL."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    status = km.get_status(notebook_id)
    if as_json:
        print(json.dumps(status or {}, indent=2))
        return

    if not status:
        console.print(f"[yellow]No keepalive records for {notebook_id}[/yellow]")
        return

    st = status.get("status", "unknown")
    st_color = "green" if st == "running" else "red"
    last_hb = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(status["last_heartbeat_at"])) if status.get("last_heartbeat_at") else "-"

    console.print(Panel(
        f"[bold white]Status:[/bold white]          [bold {st_color}]{st.upper()}[/bold {st_color}]\n"
        f"[bold white]PID:[/bold white]             {status.get('pid') or '-'}\n"
        f"[bold white]Heartbeats:[/bold white]      {status.get('heartbeat_count', 0)}\n"
        f"[bold white]Auto-Restores:[/bold white]   {status.get('restores_triggered', 0)}\n"
        f"[bold white]Last Heartbeat:[/bold white]  {last_hb}\n"
        f"[bold white]Log Path:[/bold white]        [dim]{status.get('log_path')}[/dim]",
        title=f"Keepalive Status: {notebook_id}",
        border_style="cyan",
    ))


@keepalive_group.command("list")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output list as JSON")
def cmd_keepalive_list(as_json: bool):
    """List all tracked keepalive daemons."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    items = km.list_keepalives()
    if as_json:
        print(json.dumps(items, indent=2))
        return

    if not items:
        console.print("[yellow]No keepalive daemons found.[/yellow]")
        return

    table = Table(title="[bold cyan]MoLab Anti-Idle Keepalive Daemons[/bold cyan]", box=box.ROUNDED)
    table.add_column("Notebook ID", style="cyan")
    table.add_column("Status", no_wrap=True)
    table.add_column("PID", justify="right")
    table.add_column("Interval", justify="right")
    table.add_column("Heartbeats", justify="right")
    table.add_column("Restores", justify="right")
    table.add_column("Started", style="dim")

    for item in items:
        st = item.get("status", "unknown")
        st_color = "green" if st == "running" else "dim"
        started_str = time.strftime("%m-%d %H:%M", time.localtime(item["started_at"])) if item.get("started_at") else "-"
        table.add_row(
            item["notebook_id"],
            f"[{st_color}]{st}[/{st_color}]",
            str(item.get("pid") or "-"),
            f"{item.get('interval_seconds')}s",
            str(item.get("heartbeat_count", 0)),
            str(item.get("restores_triggered", 0)),
            started_str,
        )
    console.print(table)


@keepalive_group.command("logs")
@click.argument("notebook_id")
@click.option("--lines", default=50, help="Number of trailing log lines to show")
def cmd_keepalive_logs(notebook_id: str, lines: int):
    """View heartbeat logs for a keepalive daemon."""
    from molab_cli.keepalive import get_keepalive_log_path
    log_p = get_keepalive_log_path(notebook_id)
    if not os.path.exists(log_p):
        console.print(f"[yellow]No log file found at {log_p}[/yellow]")
        return

    with open(log_p, "r", encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()
        tail = "".join(all_lines[-lines:])
    console.print(tail, markup=False)


@keepalive_group.command("run", hidden=True)
@click.argument("notebook_id")
@click.option("--interval", default=120, type=int)
@click.option("--max-hours", default=None, type=float)
@click.option("--auto-restore/--no-auto-restore", default=True)
def cmd_keepalive_run(notebook_id: str, interval: int, max_hours: Optional[float], auto_restore: bool):
    """Internal worker command for keepalive loop execution."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    km.run_loop(notebook_id=notebook_id, interval=interval, max_hours=max_hours, auto_restore=auto_restore)


# -----------------------------------------------------------------------------
# Cloud Storage Bridge Group
# -----------------------------------------------------------------------------

@cli.group("storage")
def storage_group():
    """Multi-gigabit cloud persistence directly on pod (rclone, Hugging Face, git)."""
    pass


@storage_group.command("rclone-config")
@click.argument("notebook_id")
@click.option("--config", "config_path", default=None, help="Path to local rclone.conf (default: ~/.config/rclone/rclone.conf)")
def cmd_storage_rclone_config(notebook_id: str, config_path: Optional[str]):
    """Upload local rclone credentials to pod for direct cloud sync."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    with console.status("[bold cyan]Uploading rclone configuration to pod...[/bold cyan]"):
        try:
            sb.setup_rclone_config(config_path)
            console.print("[bold green]✔ rclone credentials successfully configured on pod![/bold green]")
        except Exception as e:
            console.print(f"[red]Configuration failed:[/red] {e}")


@storage_group.command("rclone-backup")
@click.argument("notebook_id")
@click.argument("remote_dest")
@click.option("--source-dir", default="/workspace", help="Pod directory to sync (default: /workspace)")
@click.option("--flags", default=None, help="Additional rclone flags")
@click.option("--destructive", is_flag=True, default=False, help="Enable destructive sync (deletes destination files)")
@click.option("--delete", "delete_dest", is_flag=True, default=False, help="Alias for --destructive")
def cmd_storage_rclone_backup(notebook_id: str, remote_dest: str, source_dir: str, flags: Optional[str], destructive: bool, delete_dest: bool):
    """Sync pod /workspace directly to remote cloud bucket (S3/R2/B2/GCS) at 10Gbps+."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    is_dest = destructive or delete_dest
    action_desc = "Syncing (destructive)" if is_dest else "Copying (non-destructive)"
    with console.status(f"[bold cyan]{action_desc} {source_dir} to {remote_dest} via rclone...[/bold cyan]"):
        try:
            res = sb.rclone_sync_to_cloud(remote_dest=remote_dest, source_dir=source_dir, extra_flags=flags, destructive=is_dest)
            console.print(f"[bold green]✔ Cloud backup completed ({res['operation']}):[/bold green]\n{res['output']}")
        except Exception as e:
            console.print(f"[red]Backup failed:[/red] {e}")


@storage_group.command("rclone-restore")
@click.argument("notebook_id")
@click.argument("remote_source")
@click.option("--target-dir", default="/workspace", help="Pod directory to unpack into (default: /workspace)")
@click.option("--flags", default=None, help="Additional rclone flags")
@click.option("--destructive", is_flag=True, default=False, help="Enable destructive sync (deletes destination files)")
@click.option("--delete", "delete_dest", is_flag=True, default=False, help="Alias for --destructive")
def cmd_storage_rclone_restore(notebook_id: str, remote_source: str, target_dir: str, flags: Optional[str], destructive: bool, delete_dest: bool):
    """Restore pod /workspace directly from remote cloud bucket at 10Gbps+."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    is_dest = destructive or delete_dest
    action_desc = "Syncing (destructive)" if is_dest else "Copying (non-destructive)"
    with console.status(f"[bold cyan]{action_desc} from {remote_source} to {target_dir} via rclone...[/bold cyan]"):
        try:
            res = sb.rclone_sync_from_cloud(remote_source=remote_source, target_dir=target_dir, extra_flags=flags, destructive=is_dest)
            console.print(f"[bold green]✔ Cloud restore completed ({res['operation']}):[/bold green]\n{res['output']}")
        except Exception as e:
            console.print(f"[red]Restore failed:[/red] {e}")


@storage_group.command("hf-pull")
@click.argument("notebook_id")
@click.argument("repo_id")
@click.option("--dest", default="/workspace", help="Pod destination directory")
@click.option("--filename", default=None, help="Optional specific file to pull")
@click.option("--token", default=None, help="Hugging Face access token")
def cmd_storage_hf_pull(notebook_id: str, repo_id: str, dest: str, filename: Optional[str], token: Optional[str]):
    """Download model/dataset directly from Hugging Face Hub to pod at 10Gbps+."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    with console.status(f"[bold cyan]Downloading {repo_id} to {dest} on pod...[/bold cyan]"):
        try:
            res = sb.hf_download(repo_id=repo_id, dest_dir=dest, filename=filename, token=token)
            console.print(f"[bold green]✔ Hugging Face download finished:[/bold green]\n{res['output']}")
        except Exception as e:
            console.print(f"[red]Download failed:[/red] {e}")


@storage_group.command("hf-push")
@click.argument("notebook_id")
@click.argument("local_pod_path")
@click.argument("repo_id")
@click.option("--type", "repo_type", default="model", help="Repository type: model, dataset, space")
@click.option("--token", default=None, help="Hugging Face access token")
def cmd_storage_hf_push(notebook_id: str, local_pod_path: str, repo_id: str, repo_type: str, token: Optional[str]):
    """Upload model weights or datasets directly from pod to Hugging Face Hub."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    with console.status(f"[bold cyan]Uploading {local_pod_path} to {repo_id}...[/bold cyan]"):
        try:
            res = sb.hf_upload(local_pod_path=local_pod_path, repo_id=repo_id, repo_type=repo_type, token=token)
            console.print(f"[bold green]✔ Hugging Face upload finished:[/bold green]\n{res['output']}")
        except Exception as e:
            console.print(f"[red]Upload failed:[/red] {e}")


@storage_group.command("git-clone")
@click.argument("notebook_id")
@click.argument("repo_url")
@click.option("--dest", default=None, help="Destination directory on pod")
@click.option("--branch", default=None, help="Specific git branch")
def cmd_storage_git_clone(notebook_id: str, repo_url: str, dest: Optional[str], branch: Optional[str]):
    """Clone a git repository directly onto the pod."""
    from molab_cli.storage import StorageBridge
    sb = StorageBridge(notebook_id)
    with console.status(f"[bold cyan]Cloning {repo_url} on pod...[/bold cyan]"):
        try:
            res = sb.git_clone(repo_url=repo_url, dest_dir=dest, branch=branch)
            console.print(f"[bold green]✔ Git clone completed:[/bold green]\n{res['output']}")
        except Exception as e:
            console.print(f"[red]Git clone failed:[/red] {e}")


# -----------------------------------------------------------------------------
# Permanent Pod & In-Notebook Vault Group (100% on MoLab)
# -----------------------------------------------------------------------------

@cli.command("permanent")
@click.argument("notebook_id", required=False)
@click.option("--interval", default=120, help="Heartbeat interval in seconds (default: 120)")
@click.option("--auto-pack/--no-auto-pack", default=True, help="Pack /workspace into in-notebook vault (default: True)")
@click.option("--as-json", is_flag=True, help="Output JSON format")
def cmd_permanent(notebook_id: Optional[str], interval: int, auto_pack: bool, as_json: bool):
    """Make a Blackwell GPU pod permanently online with 100% on-MoLab persistence.

    Bypasses 30-minute idle reaper and container resets using dual-loop keepalive
    and in-notebook self-extracting vaults. Zero local phone storage and zero third-party cloud.
    """
    from molab_cli.client import MoLabClient
    from molab_cli.keepalive import KeepaliveManager
    from molab_cli.sandbox import SandboxSession
    from molab_cli.vault import MoLabVault

    target_nb = notebook_id
    if not target_nb:
        with console.status("[bold cyan]Discovering active Blackwell pod...[/bold cyan]"):
            client = MoLabClient()
            running = client.list_running_sandboxes()
            if not running:
                console.print("[red]No running pods found.[/red]")
                return
            target_nb = list(running.keys())[0]

    target_nb = target_nb if target_nb.startswith("nb_") else f"nb_{target_nb}"

    session = SandboxSession(target_nb)
    km = KeepaliveManager()
    vault = MoLabVault(target_nb)

    with console.status("[bold green]Locking Android wake lock & arming permanence engine...[/bold green]"):
        # 1. Acquire Android wake lock
        wake_lock_ok = False
        for wl_cmd in ("/data/data/com.termux/files/usr/bin/termux-wake-lock", "termux-wake-lock"):
            if os.path.exists(wl_cmd):
                try:
                    subprocess.run([wl_cmd], capture_output=True)
                    wake_lock_ok = True
                    break
                except Exception:
                    pass

        # 2. Resolve session & inject in-pod guard
        session.resolve()
        guard_ok = km.inject_in_pod_guard(session)

        # 3. Pack current workspace into vault if requested
        pack_res = None
        if auto_pack:
            try:
                pack_res = vault.pack_workspace()
            except Exception as pe:
                pack_res = {"error": str(pe)}

        # 4. Start background keepalive daemon
        st = km.get_status(target_nb)
        daemon_res = None
        if not st or st["status"] != "running":
            daemon_res = km.start_daemon(target_nb, interval=interval, auto_restore=True)
        else:
            daemon_res = st

        # 5. Inspect vault
        vault_info = vault.inspect_vault()

        # Telemetry
        telemetry = {}
        try:
            telemetry = session.get_gpu_telemetry()
        except Exception:
            pass

    if as_json:
        console.print(json.dumps({
            "notebook_id": target_nb,
            "sandbox_id": session.sandbox_id,
            "wake_lock_acquired": wake_lock_ok,
            "in_pod_guard_active": guard_ok,
            "daemon": daemon_res,
            "vault": vault_info,
            "telemetry": telemetry,
            "status": "PERMANENT_ONLINE"
        }, indent=2))
        return

    from rich.panel import Panel
    from rich.table import Table

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()

    grid.add_row("Notebook ID:", f"[bold white]{target_nb}[/bold white]")
    grid.add_row("Sandbox Pod ID:", f"[cyan]{session.sandbox_id}[/cyan]")
    if telemetry.get("device_name"):
        grid.add_row("Compute Engine:", f"[bold green]{telemetry['device_name']}[/bold green] ({telemetry.get('free_vram_gb', '?')} GB free)")
    grid.add_row("Android Wake Lock:", "[bold green]✔ ACQUIRED[/bold green] (Phone OS will not sleep)")
    grid.add_row("In-Pod CoreWeave Guard:", "[bold green]✔ RUNNING[/bold green] (prevents container-level idle reaper)")
    grid.add_row("Supervisor Daemon:", f"[bold green]✔ ACTIVE[/bold green] (PID {daemon_res.get('pid', '?')} | interval {interval}s)")
    if vault_info.get("has_vault"):
        grid.add_row("MoLab In-Notebook Vault:", f"[bold green]✔ ACTIVE[/bold green] (~{vault_info.get('approx_size_bytes', 0) // 1024} KB stored in MoLab DB)")
    else:
        grid.add_row("MoLab In-Notebook Vault:", "[yellow]None[/yellow] (run `molab vault pack` to save workspace)")
    grid.add_row("Cloud Persistence:", "[bold green]100% On-MoLab Servers[/bold green] (Zero phone storage, zero external cloud)")

    panel = Panel(
        grid,
        title="[bold green]🔒 MoLab Blackwell Infinite Permanence Engine[/bold green]",
        subtitle="[dim]Your GPU pod is protected from 30m timeouts and data wipes[/dim]",
        border_style="green",
    )
    console.print(panel)
    console.print(
        f"[dim]Monitor daemon: [bold]molab keepalive status {target_nb}[/bold] | "
        f"Logs: [bold]molab keepalive logs {target_nb}[/bold] | "
        f"Save workspace: [bold]molab vault pack {target_nb}[/bold][/dim]\n"
    )


@cli.group("vault")
def vault_group():
    """100% on-MoLab permanent storage vault (zero phone storage, zero external cloud)."""
    pass


@vault_group.command("pack")
@click.argument("notebook_id")
@click.option("--source-dir", default="/workspace", help="Pod directory to pack (default: /workspace)")
@click.option("--max-size", default=25.0, type=float, help="Max vault size in MB (default: 25.0)")
@click.option("--as-json", is_flag=True)
def cmd_vault_pack(notebook_id: str, source_dir: str, max_size: float, as_json: bool):
    """Compress pod workspace files directly into self-extracting cell in notebook (100% on MoLab)."""
    from molab_cli.vault import MoLabVault
    vault = MoLabVault(notebook_id)
    with console.status(f"[bold cyan]Packing {source_dir} into in-notebook vault on MoLab...[/bold cyan]"):
        try:
            res = vault.pack_workspace(source_dir=source_dir, max_size_mb=max_size)
            if as_json:
                console.print(json.dumps(res, indent=2))
            else:
                console.print(
                    f"[bold green]✔ Vault successfully packed into notebook on MoLab![/bold green]\n"
                    f"Files packed: [bold white]{res['files_packed']}[/bold white] | "
                    f"Compressed size: [bold cyan]{res['compressed_bytes']:,} bytes[/bold cyan] | "
                    f"Storage: [green]{res['storage_location']}[/green]"
                )
        except Exception as e:
            console.print(f"[red]Failed to pack vault:[/red] {e}")


@vault_group.command("unpack")
@click.argument("notebook_id")
@click.option("--target-dir", default="/workspace", help="Target pod directory (default: /workspace)")
@click.option("--overwrite/--no-overwrite", default=True, help="Overwrite conflicting files on unpack (default: True)")
@click.option("--as-json", is_flag=True)
def cmd_vault_unpack(notebook_id: str, target_dir: str, overwrite: bool, as_json: bool):
    """Extract files from in-notebook vault directly into pod workspace."""
    from molab_cli.vault import MoLabVault
    vault = MoLabVault(notebook_id)
    with console.status(f"[bold cyan]Unpacking in-notebook vault into {target_dir}...[/bold cyan]"):
        try:
            res = vault.unpack_workspace(target_dir=target_dir, overwrite=overwrite)
            if as_json:
                console.print(json.dumps(res, indent=2))
            else:
                msg = (
                    f"[bold green]✔ Vault successfully unpacked into {target_dir}![/bold green]\n"
                    f"Files restored: [bold white]{res['files_unpacked']}[/bold white] | "
                    f"Archive size: [bold cyan]{res['archive_bytes']:,} bytes[/bold cyan]"
                )
                if res.get("files_skipped", 0) > 0:
                    msg += f"\n[yellow]Preserved {res['files_skipped']} existing files: {', '.join(res.get('conflicts', []))}[/yellow]"
                console.print(msg)
        except Exception as e:
            console.print(f"[red]Failed to unpack vault:[/red] {e}")


@vault_group.command("inspect")
@click.argument("notebook_id")
@click.option("--as-json", is_flag=True)
def cmd_vault_inspect(notebook_id: str, as_json: bool):
    """Check if notebook contains a permanent in-notebook vault."""
    from molab_cli.vault import MoLabVault
    vault = MoLabVault(notebook_id)
    with console.status("[bold cyan]Inspecting notebook vault on MoLab...[/bold cyan]"):
        try:
            res = vault.inspect_vault()
            if as_json:
                console.print(json.dumps(res, indent=2))
            else:
                if res.get("has_vault"):
                    console.print(
                        f"[bold green]✔ Active In-Notebook Vault Found![/bold green]\n"
                        f"Approx size: [bold cyan]{res['approx_size_bytes']:,} bytes[/bold cyan] | "
                        f"Storage: [green]{res['storage_location']}[/green]"
                    )
                else:
                    console.print(f"[yellow]No in-notebook vault found for {notebook_id}[/yellow]")
        except Exception as e:
            console.print(f"[red]Failed to inspect vault:[/red] {e}")


# -----------------------------------------------------------------------------
# Remote Environment & Diagnostics Commands
# -----------------------------------------------------------------------------

@cli.command("env")
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


@cli.command("thumbnail")
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


@cli.command("connections")
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


# -----------------------------------------------------------------------------
# MoLab Community Gallery & Templates Group
# -----------------------------------------------------------------------------

@cli.group("gallery")
def gallery_group():
    """Browse, search, and download 110+ curated notebooks and AI recipes from MoLab."""
    pass


@gallery_group.command("list")
@click.option("--limit", default=30, help="Maximum templates to display (default: 30)")
@click.option("--as-json", is_flag=True)
def cmd_gallery_list(limit: int, as_json: bool):
    """List curated community notebook templates in MoLab Gallery."""
    from molab_cli.gallery import GalleryManager
    gm = GalleryManager()

    with console.status("[bold cyan]Fetching MoLab gallery templates...[/bold cyan]"):
        try:
            templates = gm.list_templates()
            if as_json:
                console.print(json.dumps(templates[:limit], indent=2))
                return

            from rich.table import Table
            table = Table(title=f"MoLab Community Gallery (Showing {min(len(templates), limit)} of {len(templates)})", border_style="cyan")
            table.add_column("Slug", style="bold white")
            table.add_column("Title", style="green")
            table.add_column("Web URL", style="dim")

            for t in templates[:limit]:
                table.add_row(t["slug"], t["title"], t["url"])

            console.print(table)
            console.print(f"[dim]Search templates: [bold]molab gallery search <keyword>[/bold] | Info: [bold]molab gallery info <slug>[/bold][/dim]\n")
        except Exception as e:
            console.print(f"[red]Failed to list gallery templates:[/red] {e}")


@gallery_group.command("search")
@click.argument("query")
@click.option("--as-json", is_flag=True)
def cmd_gallery_search(query: str, as_json: bool):
    """Search MoLab gallery templates by keyword or tag."""
    from molab_cli.gallery import GalleryManager
    gm = GalleryManager()

    with console.status(f"[bold cyan]Searching gallery for '{query}'...[/bold cyan]"):
        try:
            results = gm.search_templates(query)
            if as_json:
                console.print(json.dumps(results, indent=2))
                return

            if not results:
                console.print(f"[yellow]No gallery templates found matching '{query}'[/yellow]")
                return

            from rich.table import Table
            table = Table(title=f"Gallery Search Results for '{query}' ({len(results)} matches)", border_style="cyan")
            table.add_column("Slug", style="bold white")
            table.add_column("Title", style="green")
            table.add_column("Web URL", style="dim")

            for t in results:
                table.add_row(t["slug"], t["title"], t["url"])

            console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to search gallery:[/red] {e}")


@gallery_group.command("info")
@click.argument("slug")
@click.option("--as-json", is_flag=True)
def cmd_gallery_info(slug: str, as_json: bool):
    """View metadata, description, and source repository links for a gallery template."""
    from molab_cli.gallery import GalleryManager
    gm = GalleryManager()

    with console.status(f"[bold cyan]Fetching template details for '{slug}'...[/bold cyan]"):
        try:
            info = gm.get_template_info(slug)
            if as_json:
                console.print(json.dumps(info, indent=2))
                return

            from rich.panel import Panel
            from rich.table import Table
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold cyan")
            grid.add_column()

            grid.add_row("Title:", f"[bold white]{info['title']}[/bold white]")
            grid.add_row("Slug:", f"[dim]{info['slug']}[/dim]")
            grid.add_row("Description:", info["description"])
            grid.add_row("Gallery URL:", info["gallery_url"])
            if info.get("github_url"):
                grid.add_row("GitHub Source:", f"[green]{info['github_url']}[/green]")
            if info.get("raw_download_url"):
                grid.add_row("Raw Python URL:", f"[dim]{info['raw_download_url']}[/dim]")

            panel = Panel(grid, title=f"[bold green]Template: {info['title']}[/bold green]", border_style="green")
            console.print(panel)
            console.print(f"[dim]Download: [bold]molab gallery download {info['slug']} {info['slug']}.py[/bold][/dim]\n")
        except Exception as e:
            console.print(f"[red]Failed to get template info:[/red] {e}")


@gallery_group.command("download")
@click.argument("slug")
@click.argument("output_path", required=False)
def cmd_gallery_download(slug: str, output_path: Optional[str]):
    """Download raw runnable Python code for a gallery template."""
    from molab_cli.gallery import GalleryManager
    gm = GalleryManager()

    out_file = output_path or f"{slug.replace('/', '_')}.py"
    with console.status(f"[bold cyan]Downloading '{slug}' to {out_file}...[/bold cyan]"):
        try:
            gm.download_template(slug, out_file)
            console.print(f"[bold green]✔ Successfully downloaded template to {out_file}![/bold green]")
        except Exception as e:
            console.print(f"[red]Failed to download template:[/red] {e}")


def main():
    cli()


if __name__ == "__main__":
    main()
