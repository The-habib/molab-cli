"""
Rich CLI interface for molabctl.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

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
from molab_cli.sandbox import SandboxSession, LocalHttpForwarder

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
@click.version_option(version="1.0.0", prog_name="molab")
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
@click.argument("notebook_id")
@click.option("--blackwell/--cpu-only", "use_blackwell", default=True, help="Select NVIDIA RTX Pro 6000 Blackwell (96GB VRAM)")
@click.option("--cpu", default=4, help="CPU cores")
@click.option("--memory", default=32, help="Memory GiB")
def cmd_compute(notebook_id: str, use_blackwell: bool, cpu: int, memory: int):
    """Switch notebook compute resources to NVIDIA Blackwell or CPU."""
    client = MoLabClient()
    gpu = "rtxp6000" if use_blackwell else ""
    gpu_cnt = 1 if use_blackwell else 0
    hw_label = "NVIDIA RTX Pro 6000 Blackwell (96GB VRAM)" if use_blackwell else "CPU Only"

    with console.status(f"[bold yellow]Configuring compute to {hw_label} and restarting sandbox...[/bold yellow]"):
        try:
            res = client.update_compute(notebook_id, gpu=gpu, cpu=cpu, memory=memory, gpu_count=gpu_cnt)
            console.print(f"[green]✔ Compute updated successfully![/green]")
            console.print(f"  [bold]Hardware:[/bold] {hw_label}")
            console.print(f"  [bold]CPU/Memory:[/bold] {cpu} Cores / {memory} GiB RAM")
            if res.get("new_sandbox_id"):
                console.print(f"  [bold]New Sandbox Pod:[/bold] {res.get('new_sandbox_id')}")
        except Exception as e:
            console.print(f"[red]Error updating compute:[/red] {e}")


@cli.command("inspect")
@click.argument("notebook_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output inspection as JSON")
def cmd_inspect(notebook_id: str, as_json: bool):
    """Inspect notebook configuration, sandbox pod status, and hardware specs."""
    client = MoLabClient()
    session = SandboxSession(notebook_id, client=client)

    if not as_json:
        with console.status("[bold blue]Connecting to MoLab pod...[/bold blue]"):
            try:
                info = client.inspect_notebook(notebook_id)
                health = session.check_health()
            except Exception as e:
                console.print(f"[red]Error inspecting notebook:[/red] {e}")
                return
    else:
        try:
            info = client.inspect_notebook(notebook_id)
            health = session.check_health()
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return

    if as_json:
        print(json.dumps({"notebook": info, "health": health}, indent=2))
        return

    table = Table(title=f"Notebook: {info['title']} ({notebook_id})", box=box.ROUNDED)
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
@click.argument("command")
@click.option("--timeout", default=30.0, help="Execution timeout in seconds")
def cmd_exec(notebook_id: str, command: str, timeout: float):
    """Execute a bash command inside the remote CoreWeave sandbox container."""
    session = SandboxSession(notebook_id)
    with console.status(f"[bold cyan]Executing on remote pod ({session.notebook_id})...[/bold cyan]"):
        try:
            out = session.execute_command(command, timeout=timeout)
            console.print(out, markup=False)
        except Exception as e:
            from rich.markup import escape
            console.print(f"[red]Remote execution failed:[/red] {escape(str(e))}")


@cli.command("shell")
@click.argument("notebook_id")
def cmd_shell(notebook_id: str):
    """Open an interactive root bash terminal session directly in the CoreWeave pod."""
    session = SandboxSession(notebook_id)
    console.print(f"[green]Connecting interactive terminal to {session.notebook_id}...[/green]")
    console.print("[dim]Press Ctrl+D or type 'exit' to disconnect.[/dim]\n")
    try:
        session.interactive_shell()
    except Exception as e:
        console.print(f"[red]Interactive shell failed:[/red] {e}")


@cli.command("cat")
@click.argument("notebook_id")
@click.option("--cell", type=int, help="Cell index (1-indexed)")
def cmd_cat(notebook_id: str, cell: Optional[int]):
    """Print Python code of notebook or specific cell."""
    session = SandboxSession(notebook_id)
    with console.status("[bold blue]Retrieving cells from sandbox...[/bold blue]"):
        try:
            cfg, cells = session.fetch_notebook_cells()
        except Exception as e:
            console.print(f"[red]Error fetching cells:[/red] {e}")
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
@click.argument("notebook_id")
def cmd_stop(notebook_id: str):
    """Stop/Shutdown a running cloud sandbox container pod."""
    client = MoLabClient()
    with console.status(f"[bold yellow]Stopping cloud sandbox for {notebook_id}...[/bold yellow]"):
        try:
            client.stop_notebook(notebook_id)
            console.print(f"[green]✔ Cloud sandbox pod stopped for [bold]{notebook_id}[/bold][/green]")
        except Exception as e:
            console.print(f"[red]Stop failed:[/red] {e}")


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
@click.argument("notebook_id")
@click.argument("local_file", type=click.Path(exists=True))
@click.argument("remote_path", required=False)
@click.option("-r", "--recursive", is_flag=True, help="Transfer directory recursively")
def cmd_push(notebook_id: str, local_file: str, remote_path: Optional[str], recursive: bool):
    """Upload a local file or dataset directly into the CoreWeave container."""
    session = SandboxSession(notebook_id)
    with console.status(f"[bold cyan]Uploading {local_file} to pod...[/bold cyan]"):
        try:
            dest, size = session.push_file(local_file, remote_path, recursive=recursive)
            console.print(f"[green]✔ Uploaded [bold]{local_file}[/bold] -> [bold]{dest}[/bold] ({size} bytes)[/green]")
        except Exception as e:
            console.print(f"[red]Upload failed:[/red] {e}")


@cli.command("pull")
@click.argument("notebook_id")
@click.argument("remote_path")
@click.argument("local_file", required=False)
@click.option("-r", "--recursive", is_flag=True, help="Transfer directory recursively")
def cmd_pull(notebook_id: str, remote_path: str, local_file: Optional[str], recursive: bool):
    """Download a file from the CoreWeave container to local storage."""
    session = SandboxSession(notebook_id)
    with console.status(f"[bold cyan]Downloading {remote_path} from pod...[/bold cyan]"):
        try:
            dest, size = session.pull_file(remote_path, local_file, recursive=recursive)
            console.print(f"[green]✔ Downloaded [bold]{remote_path}[/bold] -> [bold]{dest}[/bold] ({size} bytes)[/green]")
        except Exception as e:
            console.print(f"[red]Download failed:[/red] {e}")


@cli.command("gpu")
@click.argument("notebook_id")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output GPU telemetry as JSON")
def cmd_gpu(notebook_id: str, as_json: bool):
    """Display real-time NVIDIA Blackwell GPU telemetry and VRAM utilization."""
    session = SandboxSession(notebook_id)
    if not as_json:
        with console.status("[bold green]Querying NVIDIA Blackwell GPU telemetry...[/bold green]"):
            try:
                telemetry = session.get_gpu_telemetry()
            except Exception as e:
                console.print(f"[red]Failed to query GPU telemetry:[/red] {e}")
                return
    else:
        try:
            telemetry = session.get_gpu_telemetry()
            print(json.dumps(telemetry, indent=2))
            return
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            return

    if not telemetry.get("cuda_available"):
        console.print(Panel(
            "[yellow]No NVIDIA GPU detected on this instance (Instance is in CPU-only mode).[/yellow]\n\n"
            f"To switch this notebook to NVIDIA Blackwell GPU:\n"
            f"[bold green]molabctl compute {notebook_id} --blackwell[/bold green]",
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
@click.argument("notebook_id")
@click.argument("packages", nargs=-1, required=True)
def cmd_install(notebook_id: str, packages: tuple):
    """Install Python packages inside the remote pod environment using uv/pip."""
    session = SandboxSession(notebook_id)
    pkgs_list = list(packages)
    with console.status(f"[bold cyan]Installing {', '.join(pkgs_list)} in pod...[/bold cyan]"):
        try:
            out = session.install_packages(pkgs_list)
            if out:
                console.print(out)
            console.print(f"[green]✔ Successfully installed: {' '.join(pkgs_list)}[/green]")
        except Exception as e:
            console.print(f"[red]Installation failed:[/red] {e}")


@cli.command("forward")
@click.argument("notebook_id")
@click.option("--port", default=8000, help="Local port to bind on localhost (default 8000)")
def cmd_forward(notebook_id: str, port: int):
    """Bridge local HTTP port (localhost:8000) directly to the cloud model server."""
    session = SandboxSession(notebook_id)
    with console.status("[bold green]Verifying remote model server on Blackwell pod...[/bold green]"):
        try:
            if not session.ensure_model_server_running():
                console.print("[yellow]Warning: Model server did not report healthy. Proceeding anyway...[/yellow]")
        except Exception as e:
            console.print(f"[red]Error verifying server:[/red] {e}")

    forwarder = LocalHttpForwarder(session, port=port)
    console.print(Panel(
        f"[bold green]🚀 Localhost Bridge Active![/bold green]\n\n"
        f"• Local OpenAI Base URL: [bold cyan]http://localhost:{port}/v1[/bold cyan]\n"
        f"• Chat Completions:     [bold cyan]http://localhost:{port}/v1/chat/completions[/bold cyan]\n"
        f"• Model Health Check:   [bold cyan]http://localhost:{port}/health[/bold cyan]\n"
        f"• Deployed Model:       [bold green]gemma-3-27b-it-abliterated (Uncompressed BF16)[/bold green]\n"
        f"• Cloud Hardware:       [bold green]NVIDIA RTX PRO 6000 Blackwell (95GB VRAM)[/bold green]\n\n"
        f"[dim]You can now use curl, Python 'openai' client, Open WebUI, or SillyTavern pointing to http://localhost:{port}/v1[/dim]\n"
        f"[dim]Press [bold]Ctrl+C[/bold] to stop the forwarder.[/dim]",
        title="MoLab Localhost Bridge",
        border_style="green",
    ))
    try:
        forwarder.start()
    except KeyboardInterrupt:
        console.print("\n[dim]Forwarder stopped.[/dim]")


@cli.command("chat")
@click.argument("notebook_id")
@click.option("--max-tokens", default=512, help="Max tokens to generate")
@click.option("--temp", default=0.7, help="Sampling temperature")
def cmd_chat(notebook_id: str, max_tokens: int, temp: float):
    """Interactive streaming chat with the 27B unrestricted model directly in terminal."""
    session = SandboxSession(notebook_id)
    with console.status("[bold green]Connecting to 27B model on Blackwell pod...[/bold green]"):
        try:
            if not session.ensure_model_server_running():
                console.print("[red]Could not verify model server on pod. Ensure server is started with:[/red]")
                console.print("[dim]molab exec " + notebook_id + " '/marimo/start_server.sh'[/dim]")
                return
        except Exception as e:
            console.print(f"[red]Connection error:[/red] {e}")
            return

    console.print(Panel(
        "[bold green]Connected to Gemma 3 27B Abliterated (Unrestricted / Uncompressed)[/bold green]\n"
        "Hardware: [bold]NVIDIA RTX PRO 6000 Blackwell (94.97 GB VRAM)[/bold]\n"
        "Type your message and press Enter. Type [bold red]exit[/bold red] or [bold red]/quit[/bold red] to end.",
        title="Interactive Terminal Chat",
        border_style="green"
    ))

    history = []
    while True:
        try:
            user_input = console.input("\n[bold cyan]User > [/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session closed.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Goodbye![/dim]")
            break

        history.append({"role": "user", "content": user_input})
        with console.status("[bold green]Generating response on Blackwell GPU...[/bold green]"):
            try:
                res = session.chat_completion(history, max_tokens=max_tokens, temperature=temp)
                reply = res.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                console.print(f"[red]Generation failed:[/red] {e}")
                continue

        history.append({"role": "assistant", "content": reply})
        console.print(Panel(
            Markdown(reply),
            title="[bold green]Gemma 3 27B[/bold green]",
            border_style="green",
        ))


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


def main():
    cli()


if __name__ == "__main__":
    main()
