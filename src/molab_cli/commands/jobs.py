"""
Batch orchestrator, background jobs, workload, and notification commands.
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


@click.group("job")
def job_group():
    """Manage asynchronous background jobs and output artifacts."""
    pass



@job_group.command("submit")
@click.argument("target_or_cmd")
@click.argument("command", required=False)
@click.option("--name", default=None, help="Descriptive job name")
@click.option("--workdir", default="/workspace", help="Remote working directory")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output job record as JSON")
def cmd_job_submit(target_or_cmd: str, command: Optional[str], name: Optional[str], workdir: str, as_json: bool):
    """Submit a detached background job to pod with persistent SQLite tracking."""
    client = MoLabClient()
    if command is None:
        cmd = target_or_cmd
        try:
            target_nb = resolve_target_notebook(client, None, require_running=True)
        except Exception as e:
            handle_cli_error(e, title="Job Submit Target Resolution Failed")
            return
    else:
        cmd = command
        try:
            target_nb = resolve_target_notebook(client, target_or_cmd, require_running=True)
        except Exception as e:
            handle_cli_error(e, title="Job Submit Target Resolution Failed")
            return

    from molab_cli.jobs import JobManager
    jm = JobManager()
    job = jm.submit_job(notebook_id=target_nb, command=cmd, name=name, workdir=workdir)
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



@click.group("workload")
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



@click.group("batch")
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



@click.group("notify")
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



JOBS_COMMANDS = [
    job_group,
    workload_group,
    batch_group,
    notify_group,
]
