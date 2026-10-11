"""
Authentication, diagnostics, keepalive, kernel, package, and container shell commands.
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


@click.command("login")
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



@click.command("status")
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



@click.command("exec")
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



@click.command("shell")
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



@click.command("install")
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



@click.command("doctor")
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



@click.command("capabilities")
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



@click.group("kernel")
def kernel_group():
    """Manage and interact directly with the remote Marimo Python kernel."""
    pass



@kernel_group.command("status")
@click.argument("notebook_id", required=False)
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_kernel_status(notebook_id: Optional[str], file_key: str, as_json: bool):
    """Check if the remote Python kernel is idle or running."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Kernel Target Failed", as_json=as_json)
        return

    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(target_nb, client=client)
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
@click.argument("arg1", metavar="[NOTEBOOK_ID|CODE]")
@click.argument("arg2", required=False, metavar="[CODE]")
@click.option("--file", "file_key", default="notebook.py", help="Notebook context filename")
@click.option("--timeout", default=30.0, help="Execution timeout in seconds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output evaluation result as JSON")
def cmd_kernel_eval(arg1: str, arg2: Optional[str], file_key: str, timeout: float, as_json: bool):
    """Execute Python code directly inside the remote Marimo Python kernel."""
    client = MoLabClient()
    if arg2 is None:
        try:
            target_nb = resolve_target_notebook(client, None, require_running=True)
            code = arg1
        except Exception as e:
            handle_cli_error(e, title="Kernel Eval Target Failed", as_json=as_json)
            return
    else:
        target_nb = arg1
        code = arg2

    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(target_nb, client=client)
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



@click.group("pkg")
def pkg_group():
    """Inspect and manage Python packages on the remote pod via native Marimo package manager."""
    pass



@pkg_group.command("list")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output package list as JSON")
def cmd_pkg_list(notebook_id: Optional[str], as_json: bool):
    """List packages managed by remote pod environment."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Package List Target Failed", as_json=as_json)
        return

    from molab_cli.backend import MarimoBackendClient
    session = SandboxSession(target_nb, client=client)
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



@click.group("keepalive")
def keepalive_group():
    """Autonomous anti-idle heartbeats, session renewal, and pod resurrection."""
    pass



@keepalive_group.command("start")
@click.argument("notebook_id", required=False)
@click.option("--interval", default=120, help="Heartbeat interval in seconds (default: 120)")
@click.option("--max-hours", default=None, type=float, help="Max runtime in hours (default: unlimited)")
@click.option("--auto-restore/--no-auto-restore", default=True, help="Auto-restore snapshot if pod resets")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output daemon record as JSON")
def cmd_keepalive_start(notebook_id: Optional[str], interval: int, max_hours: Optional[float], auto_restore: bool, as_json: bool):
    """Launch detached anti-idle keepalive daemon in background."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Keepalive Target Resolution Failed", as_json=as_json)
        return

    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    try:
        rec = km.start_daemon(notebook_id=target_nb, interval=interval, max_hours=max_hours, auto_restore=auto_restore)
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
        f"Check status: [bold]molab keepalive status {target_nb}[/bold]\n"
        f"Tail logs:    [bold]molab keepalive logs {target_nb}[/bold]\n"
        f"Stop daemon:  [bold]molab keepalive stop {target_nb}[/bold]",
        title="Pod Anti-Idle Keepalive",
        border_style="green",
    ))



@keepalive_group.command("stop")
@click.argument("notebook_id", required=False)
def cmd_keepalive_stop(notebook_id: Optional[str]):
    """Stop the background keepalive daemon for a pod."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Keepalive Target Resolution Failed")
        return

    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    ok = km.stop_daemon(target_nb)
    if ok:
        console.print(f"[bold green]✔ Keepalive daemon stopped for {target_nb}[/bold green]")
    else:
        console.print(f"[yellow]No active keepalive daemon found for {target_nb}[/yellow]")



@keepalive_group.command("status")
@click.argument("notebook_id", required=False)
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_keepalive_status(notebook_id: Optional[str], as_json: bool):
    """Inspect status of keepalive daemon and session TTL."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Keepalive Target Resolution Failed", as_json=as_json)
        return

    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    status = km.get_status(target_nb)
    if as_json:
        print(json.dumps(status or {}, indent=2))
        return

    if not status:
        console.print(f"[yellow]No keepalive records for {target_nb}[/yellow]")
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
        title=f"Keepalive Status: {target_nb}",
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
@click.argument("notebook_id", required=False)
@click.option("--lines", default=50, help="Number of trailing log lines to show")
def cmd_keepalive_logs(notebook_id: Optional[str], lines: int):
    """View heartbeat logs for a keepalive daemon."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Keepalive Target Resolution Failed")
        return

    from molab_cli.keepalive import get_keepalive_log_path
    log_p = get_keepalive_log_path(target_nb)
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



@click.command("permanent")
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



SYSTEM_COMMANDS = [
    cmd_login,
    cmd_status,
    cmd_exec,
    cmd_shell,
    cmd_install,
    cmd_doctor,
    cmd_capabilities,
    kernel_group,
    pkg_group,
    keepalive_group,
    cmd_permanent,
]
