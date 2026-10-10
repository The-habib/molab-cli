"""
Interactive Control Center & Modern TUI Dashboard for MoLab CLI.
Provides zero-typing navigation, pod management, AI model controls,
100% on-MoLab vault persistence, community gallery explorer, and job monitoring.
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional

import questionary
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from molab_cli import __version__
from molab_cli.auth import inspect_auth_status, save_and_verify_auth
from molab_cli.backend import MarimoBackendClient
from molab_cli.client import MoLabClient
from molab_cli.config import save_config
from molab_cli.gallery import GalleryManager
from molab_cli.jobs import JobManager
from molab_cli.keepalive import KeepaliveManager
from molab_cli.onboarding import ensure_authenticated, run_onboarding_wizard
from molab_cli.sandbox import LocalHttpForwarder, SandboxSession
from molab_cli.theme import (
    COLOR_BLACKWELL,
    COLOR_PRIMARY,
    QUESTIONARY_STYLE,
    console,
    render_banner,
    render_error_card,
    render_info_card,
    render_status_bar,
    render_success_card,
    render_warning_card,
)
from molab_cli.vault import MoLabVault
from molab_cli.workloads import WorkloadRegistry


def pick_notebook(client: MoLabClient, title: str = "Select a notebook:") -> Optional[Dict[str, Any]]:
    """Interactive notebook selector with smart occupancy audits."""
    with console.status("[bold cyan]Auditing notebooks and Blackwell pods...[/bold cyan]"):
        try:
            notebooks = client.list_notebooks()
            running = client.list_running_sandboxes()
        except Exception as e:
            render_error_card("Failed to Fetch Notebooks", str(e))
            return None

    if not notebooks:
        console.print("[yellow]No notebooks found in your workspace.[/yellow]")
        return None

    choices = []
    for nb in notebooks:
        nb_id = nb["id"]
        t = nb.get("title", "Untitled")
        gpu = nb.get("gpu", "")
        is_running = nb_id in running

        hw_tag = "⚡ Blackwell 96GB" if "rtx" in gpu.lower() or "blackwell" in gpu.lower() else "CPU"

        if is_running:
            try:
                sess = SandboxSession(nb_id, client=client)
                wl = sess.get_workload_status()
                is_free = not wl.get("is_occupied", False)
                status_tag = "🟢 ★ FREE" if is_free else "🟡 ⚠️ OCCUPIED"
            except Exception:
                status_tag = "🟢 RUNNING"
        else:
            status_tag = "⚪ STOPPED"

        label = f"{status_tag} | {t} ({hw_tag}) [{nb_id}]"
        choices.append(questionary.Choice(title=label, value=nb))

    choices.append(questionary.Choice(title="⬅️ Cancel / Back", value=None))

    selected = questionary.select(
        title,
        choices=choices,
        style=QUESTIONARY_STYLE,
    ).ask()

    return selected


def action_browse_notebooks(client: MoLabClient) -> None:
    """Submenu for viewing and managing notebooks."""
    while True:
        with console.status("[bold cyan]Loading notebooks...[/bold cyan]"):
            try:
                notebooks = client.list_notebooks()
                running = client.list_running_sandboxes()
            except Exception as e:
                render_error_card("Error Fetching Notebooks", str(e))
                return

        # Render Table
        table = Table(
            title="[bold cyan]📓 Cloud Notebooks Workspace (NVIDIA Blackwell Fabric)[/bold cyan]",
            border_style="cyan",
            expand=True,
        )
        table.add_column("Status", justify="center", width=14)
        table.add_column("Notebook Title", style="bold white", ratio=2)
        table.add_column("Compute Hardware", justify="center", width=22)
        table.add_column("ID", style="dim", width=26)

        for nb in notebooks:
            is_running = nb["id"] in running
            gpu = nb.get("gpu", "")
            status_text = "[bold green]🟢 RUNNING[/bold green]" if is_running else "[dim]⚪ STOPPED[/dim]"
            hw_text = "[bold #76b900]⚡ Blackwell 96GB[/bold #76b900]" if "rtx" in gpu.lower() else "[cyan]4 vCPU / 32GB[/cyan]"
            table.add_row(status_text, nb.get("title", "Untitled"), hw_text, nb["id"])

        console.print(table)

        choices = [
            "🔍 Select Notebook for Actions (Terminal, Vault, AI, Telemetry)",
            "🚀 Create New Notebook Pod",
            "⬅️ Back to Main Menu",
        ]
        action = questionary.select("Notebook Management:", choices=choices, style=QUESTIONARY_STYLE).ask()

        if not action or "Back" in action:
            break
        elif "Create New" in action:
            action_create_notebook(client)
        elif "Select Notebook" in action:
            nb = pick_notebook(client, "Choose a notebook to inspect/manage:")
            if nb:
                manage_single_notebook(client, nb)


def manage_single_notebook(client: MoLabClient, nb: Dict[str, Any]) -> None:
    """Comprehensive action menu for a specific selected notebook."""
    nb_id = nb["id"]

    while True:
        with console.status("[bold cyan]Refreshing notebook state...[/bold cyan]"):
            try:
                info = client.inspect_notebook(nb_id)
            except Exception as e:
                render_error_card("Notebook Inspection Failed", str(e))
                return

        title = info.get("title", "Untitled")
        gpu = info.get("gpu", "")
        sb_id = info.get("sandbox_id", "Not Provisioned")
        hw_label = "NVIDIA RTX PRO 6000 Blackwell Server Edition (96GB VRAM)" if "rtx" in gpu.lower() else "CPU (4 vCPUs, 32GB RAM)"

        panel_content = Text()
        panel_content.append("Title:    ", style="dim")
        panel_content.append(f"{title}\n", style="bold white")
        panel_content.append("ID:       ", style="dim")
        panel_content.append(f"{nb_id}\n", style="cyan")
        panel_content.append("Compute:  ", style="dim")
        panel_content.append(f"{hw_label}\n", style="bold #76b900" if "rtx" in gpu.lower() else "white")
        panel_content.append("Sandbox:  ", style="dim")
        panel_content.append(f"{sb_id}\n", style="dim")

        console.print(Panel(
            panel_content,
            title=f"[bold cyan]⚙️ Notebook: {title}[/bold cyan]",
            border_style="cyan",
        ))

        choices = [
            "💻 Open Interactive Root Terminal (bash)",
            "💬 Chat with Deployed 27B LLM (Terminal Chat)",
            "🌐 Start Localhost Bridge (port 8000 -> http://localhost:8000/v1)",
            "🛡️ In-Notebook Vault: Pack Workspace (100% on MoLab)",
            "📦 In-Notebook Vault: Unpack / Restore Workspace",
            "⏱️ Make Pod Permanent (Anti-Idle & Supervisor)",
            "📊 Real-time GPU & Host Telemetry",
            "🔬 Remote Container Environment Diagnostics",
            "🖼️ Generate Open Graph SVG Thumbnail",
            "⚡ Switch Compute (NVIDIA Blackwell 96GB ⟷ CPU)",
            "📝 View Python Code (cat)",
            "📦 Install Python Packages",
            "🛑 Stop / Shutdown Sandbox Pod",
            "🔄 Duplicate / Clone Notebook",
            "✏️ Rename Notebook",
            "🗑️ Delete Notebook",
            "⬅️ Back",
        ]

        choice = questionary.select("Action for this notebook:", choices=choices, style=QUESTIONARY_STYLE).ask()
        if not choice or "Back" in choice:
            break

        session = SandboxSession(nb_id, client=client)

        if "Open Interactive Root Terminal" in choice:
            console.print(f"[bold cyan]Connecting to interactive bash on {title}...[/bold cyan]")
            try:
                session.interactive_shell()
            except Exception as e:
                render_error_card("Terminal Connection Error", str(e))

        elif "Chat with Deployed 27B LLM" in choice:
            action_terminal_chat(nb_id)

        elif "Start Localhost Bridge" in choice:
            action_localhost_bridge(nb_id)

        elif "Pack Workspace" in choice:
            with console.status("[bold cyan]Packing /workspace into in-notebook vault on MoLab...[/bold cyan]"):
                try:
                    vault = MoLabVault(session)
                    res = vault.pack_workspace()
                    render_success_card(
                        "Vault Packed Successfully!",
                        f"Status: {res['status']}\nStored in /marimo/notebook.py metadata.\nZero local phone storage used."
                    )
                except Exception as e:
                    render_error_card("Vault Pack Failed", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Unpack" in choice:
            with console.status("[bold cyan]Unpacking in-notebook vault into /workspace...[/bold cyan]"):
                try:
                    vault = MoLabVault(session)
                    res = vault.unpack_workspace()
                    render_success_card("Vault Restored!", f"Files restored into /workspace successfully.")
                except Exception as e:
                    render_error_card("Vault Unpack Failed", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Make Pod Permanent" in choice:
            with console.status("[bold green]Arming 24/7 permanence engine...[/bold green]"):
                try:
                    km = KeepaliveManager()
                    d = km.start_daemon(notebook_id=nb_id, auto_restore=True)
                    render_success_card(
                        "Pod Made Permanent!",
                        f"Daemon PID: {d['pid']}\nHeartbeat every 120s.\nAuto-restores if pod resets."
                    )
                except Exception as e:
                    render_error_card("Permanence Failed", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Real-time GPU & Host Telemetry" in choice:
            display_gpu_telemetry(nb_id)

        elif "Remote Container Environment Diagnostics" in choice:
            with console.status("[bold cyan]Fetching container runtime specifications...[/bold cyan]"):
                try:
                    backend = MarimoBackendClient(session)
                    env = backend.get_environment()
                    t = Table(title=f"Remote Environment: {title}", border_style="cyan")
                    t.add_column("Property", style="bold white")
                    t.add_column("Value", style="green")
                    t.add_row("Operating System", f"{env.get('OS', 'Linux')} ({env.get('OS Version', '')})")
                    t.add_row("Python Runtime", env.get("Python Version", "3.13"))
                    t.add_row("Node.js Runtime", env.get("Binaries", {}).get("Node", "v22"))
                    t.add_row("uv Package Manager", env.get("Binaries", {}).get("uv", "0.12.1"))
                    console.print(t)
                except Exception as e:
                    render_error_card("Environment Query Failed", str(e))
            questionary.text("Press Enter to return...", style=QUESTIONARY_STYLE).ask()

        elif "Generate Open Graph SVG Thumbnail" in choice:
            out_file = f"thumbnail_{nb_id[:8]}.svg"
            with console.status(f"[bold cyan]Generating visual thumbnail ({out_file})...[/bold cyan]"):
                try:
                    backend = MarimoBackendClient(session)
                    backend.get_thumbnail(output_path=out_file)
                    render_success_card("Thumbnail Generated!", f"Saved to local file: [bold white]{out_file}[/bold white]")
                except Exception as e:
                    render_error_card("Thumbnail Error", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Switch Compute" in choice:
            new_hw = questionary.select(
                "Choose compute resource:",
                choices=[
                    "⚡ NVIDIA RTX PRO 6000 Blackwell (96 GB VRAM)",
                    "🖥️ CPU-only instance (4 vCPU, 32GB RAM)",
                    "⬅️ Cancel",
                ],
                style=QUESTIONARY_STYLE,
            ).ask()
            if new_hw and "Blackwell" in new_hw:
                with console.status("[bold green]Provisioning NVIDIA Blackwell Server Edition...[/bold green]"):
                    try:
                        res = client.update_compute(nb_id, gpu="rtxp6000")
                        render_success_card("Switched to Blackwell GPU!", f"Pod restarted with 96GB GDDR7 VRAM.\nNew Sandbox: {res.get('new_sandbox_id')}")
                    except Exception as e:
                        render_error_card("Compute Update Failed", str(e))
            elif new_hw and "CPU-only" in new_hw:
                with console.status("[bold cyan]Switching to CPU instance...[/bold cyan]"):
                    try:
                        res = client.update_compute(nb_id, gpu="none")
                        render_success_card("Switched to CPU", "Pod restarted in CPU mode.")
                    except Exception as e:
                        render_error_card("Compute Update Failed", str(e))

        elif "View Python Code" in choice:
            with console.status("[bold cyan]Fetching code...[/bold cyan]"):
                try:
                    cfg, cells = session.fetch_notebook_cells()
                    if cells:
                        for idx, cell in enumerate(cells, 1):
                            code = cell.get("code", "")
                            console.print(Panel(
                                Markdown(f"```python\n{code}\n```"),
                                title=f"Cell {idx} ({cell.get('name', 'unnamed')})",
                                border_style="dim cyan",
                            ))
                    else:
                        console.print("[dim]No cells found in notebook.[/dim]")
                except Exception as e:
                    render_error_card("Code Fetch Failed", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Install Python Packages" in choice:
            pkgs = questionary.text("Enter package names to install (e.g. transformers torch einops):", style=QUESTIONARY_STYLE).ask()
            if pkgs and pkgs.strip():
                with console.status(f"[bold cyan]Installing '{pkgs}' in remote pod...[/bold cyan]"):
                    try:
                        out = session.execute_command(f"uv pip install {pkgs.strip()}", timeout=120.0)
                        console.print(Panel(out, title="Installation Output", border_style="cyan"))
                    except Exception as e:
                        render_error_card("Install Failed", str(e))

        elif "Stop / Shutdown Sandbox Pod" in choice:
            confirm = questionary.confirm("Stop this pod to free resources?", default=True, style=QUESTIONARY_STYLE).ask()
            if confirm:
                with console.status("[bold yellow]Stopping container pod...[/bold yellow]"):
                    try:
                        client.stop_notebook(nb_id)
                        render_success_card("Pod Stopped", f"Container for {title} stopped.")
                        break
                    except Exception as e:
                        render_error_card("Stop Failed", str(e))


def action_community_gallery(client: MoLabClient) -> None:
    """Interactive explorer for MoLab 111+ community neural recipes."""
    gm = GalleryManager()
    while True:
        choices = [
            "🔍 Search Recipes by Keyword (diffusion, chat, audio, etc.)",
            "📋 Browse Top Community Recipes",
            "⬅️ Back to Main Menu",
        ]
        choice = questionary.select("MoLab Community Gallery:", choices=choices, style=QUESTIONARY_STYLE).ask()
        if not choice or "Back" in choice:
            break

        templates = []
        if "Search" in choice:
            q = questionary.text("Enter search query:", style=QUESTIONARY_STYLE).ask()
            if not q:
                continue
            with console.status(f"[bold cyan]Searching for '{q}'...[/bold cyan]"):
                templates = gm.search_templates(q)
        else:
            with console.status("[bold cyan]Fetching community templates...[/bold cyan]"):
                templates = gm.list_templates()[:25]

        if not templates:
            console.print("[yellow]No templates found.[/yellow]")
            continue

        template_choices = [
            questionary.Choice(title=f"⚡ {t['title']} ({t['slug']})", value=t)
            for t in templates
        ]
        template_choices.append(questionary.Choice(title="⬅️ Back", value=None))

        selected = questionary.select("Select a recipe to inspect/download:", choices=template_choices, style=QUESTIONARY_STYLE).ask()
        if not selected:
            continue

        slug = selected["slug"]
        info = gm.get_template_info(slug)
        console.print(Panel(
            f"[bold white]{info['title']}[/bold white]\n"
            f"[dim]Slug: {info['slug']}[/dim]\n\n"
            f"{info['description']}\n\n"
            f"[cyan]Gallery URL:[/cyan] {info['gallery_url']}\n"
            f"[green]GitHub Source:[/green] {info.get('github_url', 'N/A')}",
            title="[bold green]Recipe Details[/bold green]",
            border_style="green",
        ))

        act = questionary.select(
            "Action for this recipe:",
            choices=[
                "⬇️ Download Python Code Locally",
                "🚀 Download & Push Directly to Pod",
                "⬅️ Back",
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if act and "Download Python Code Locally" in act:
            out_file = f"{slug.replace('/', '_')}.py"
            gm.download_template(slug, out_file)
            render_success_card("Downloaded!", f"Saved template to: [bold white]{out_file}[/bold white]")
        elif act and "Push Directly to Pod" in act:
            nb = pick_notebook(client, "Select pod destination:")
            if nb:
                temp_file = f"/tmp/{slug.replace('/', '_')}.py"
                gm.download_template(slug, temp_file)
                sess = SandboxSession(nb["id"], client=client)
                sess.push_file(temp_file, f"/workspace/{slug.replace('/', '_')}.py")
                render_success_card("Deployed to Pod!", f"Uploaded to /workspace/{slug.replace('/', '_')}.py")


def action_vault_permanence(client: MoLabClient) -> None:
    """Dedicated hub for 100% on-MoLab vault and anti-idle supervision."""
    km = KeepaliveManager()
    while True:
        choices = [
            "🛡️ 1-Click Make Pod Permanent (Infinite Anti-Idle + Vault)",
            "📦 Pack Workspace to Cloud Vault (0 Phone Storage)",
            "📂 Unpack Workspace from Cloud Vault",
            "🔍 Inspect In-Notebook Vault on MoLab",
            "📊 View Keepalive Supervisor Daemons & Logs",
            "🛑 Stop Keepalive Daemon",
            "⬅️ Back to Main Menu",
        ]
        choice = questionary.select("100% On-MoLab Permanence Hub:", choices=choices, style=QUESTIONARY_STYLE).ask()
        if not choice or "Back" in choice:
            break

        if "Make Pod Permanent" in choice:
            nb = pick_notebook(client, "Select pod to make permanent:")
            if nb:
                with console.status("[bold green]Arming permanent supervisor...[/bold green]"):
                    try:
                        d = km.start_daemon(notebook_id=nb["id"], auto_restore=True)
                        render_success_card("Permanent Engine Armed!", f"Pod {nb['id']} is now guarded against the 30-minute reaper.")
                    except Exception as e:
                        render_error_card("Error", str(e))

        elif "Pack Workspace" in choice:
            nb = pick_notebook(client, "Select pod to pack:")
            if nb:
                with console.status("[bold cyan]Compressing /workspace into in-notebook vault...[/bold cyan]"):
                    try:
                        sess = SandboxSession(nb["id"], client=client)
                        v = MoLabVault(sess)
                        res = v.pack_workspace()
                        render_success_card("Vault Packed!", f"Compressed {res.get('compressed_bytes', 0):,} bytes into /marimo/notebook.py metadata.")
                    except Exception as e:
                        render_error_card("Vault Error", str(e))

        elif "Unpack Workspace" in choice:
            nb = pick_notebook(client, "Select pod to unpack:")
            if nb:
                with console.status("[bold cyan]Extracting cloud vault into /workspace...[/bold cyan]"):
                    try:
                        sess = SandboxSession(nb["id"], client=client)
                        v = MoLabVault(sess)
                        res = v.unpack_workspace()
                        render_success_card("Vault Restored!", "Files extracted successfully.")
                    except Exception as e:
                        render_error_card("Vault Error", str(e))

        elif "Inspect" in choice:
            nb = pick_notebook(client, "Select pod to inspect:")
            if nb:
                sess = SandboxSession(nb["id"], client=client)
                v = MoLabVault(sess)
                status = v.inspect_vault()
                console.print(Panel(
                    f"Vault Exists: {'[bold green]YES[/bold green]' if status['exists'] else '[dim]NO[/dim]'}\n"
                    f"Size: {status.get('size_bytes', 0):,} bytes\n"
                    f"Last Saved: {status.get('updated_at', 'Unknown')}",
                    title="Vault Metadata",
                    border_style="cyan",
                ))

        elif "View Keepalive Supervisor" in choice:
            daemons = km.list_daemons()
            if not daemons:
                console.print("[dim]No active keepalive daemons.[/dim]")
            else:
                t = Table(title="Keepalive Supervisors", border_style="green")
                t.add_column("Pod ID", style="bold white")
                t.add_column("PID", style="cyan")
                t.add_column("Heartbeats", style="green")
                t.add_column("Status", style="bold green")
                for d in daemons:
                    t.add_row(d["notebook_id"], str(d["pid"]), str(d.get("heartbeat_count", 0)), d["status"])
                console.print(t)

        elif "Stop Keepalive" in choice:
            nb = pick_notebook(client, "Select pod to stop daemon:")
            if nb:
                km.stop_daemon(nb["id"])
                render_success_card("Stopped", "Keepalive daemon stopped.")


def action_jobs_and_batches(client: MoLabClient) -> None:
    """View and manage asynchronous SQLite background jobs."""
    jm = JobManager()
    while True:
        jobs = jm.list_jobs(limit=15)
        t = Table(title="Recent Background Compute Jobs (SQLite)", border_style="cyan", expand=True)
        t.add_column("ID", style="dim", width=12)
        t.add_column("Status", justify="center", width=12)
        t.add_column("Job Name", style="bold white")
        t.add_column("Command", style="cyan", ratio=2)
        t.add_column("Pod", style="dim", width=14)

        for j in jobs:
            st = j["status"]
            color = "green" if st == "COMPLETED" else "cyan" if st == "RUNNING" else "red" if st == "FAILED" else "dim"
            t.add_row(j["id"][:10], f"[{color}]{st}[/{color}]", j.get("name") or "Unnamed", j["command"][:40], (j.get("notebook_id") or "")[:10])

        console.print(t)

        choices = [
            "📜 View Trailing Logs for a Job",
            "🛑 Cancel a Running Job",
            "🚀 Submit Quick Background Command",
            "⬅️ Back to Main Menu",
        ]
        act = questionary.select("Job Actions:", choices=choices, style=QUESTIONARY_STYLE).ask()
        if not act or "Back" in act:
            break

        if "View Trailing Logs" in act:
            job_choices = [questionary.Choice(title=f"{j['id'][:10]} - {j.get('name', 'Job')}", value=j["id"]) for j in jobs]
            job_choices.append(questionary.Choice(title="⬅️ Cancel", value=None))
            jid = questionary.select("Select job:", choices=job_choices, style=QUESTIONARY_STYLE).ask()
            if jid:
                logs = jm.get_job_logs(jid, lines=40)
                console.print(Panel(logs or "No logs recorded.", title=f"Logs for {jid}", border_style="dim cyan"))
                questionary.text("Press Enter to return...", style=QUESTIONARY_STYLE).ask()

        elif "Cancel" in act:
            running_jobs = [j for j in jobs if j["status"] == "RUNNING"]
            if not running_jobs:
                console.print("[dim]No running jobs to cancel.[/dim]")
                continue
            jid = questionary.select("Select job to cancel:", choices=[j["id"] for j in running_jobs], style=QUESTIONARY_STYLE).ask()
            if jid:
                jm.cancel_job(jid)
                render_success_card("Cancelled", f"Job {jid} cancelled.")

        elif "Submit Quick" in act:
            nb = pick_notebook(client, "Select target pod:")
            if nb:
                cmd = questionary.text("Enter command to run in background:", style=QUESTIONARY_STYLE).ask()
                if cmd:
                    job = jm.submit_job(notebook_id=nb["id"], command=cmd, name="manual-tui-job")
                    render_success_card("Job Submitted!", f"Job ID: {job['id']}\nTracking in SQLite.")


def action_workloads(client: MoLabClient) -> None:
    """Pre-configured AI Workload Launcher."""
    reg = WorkloadRegistry()
    workloads = reg.list_workloads()

    choices = [
        questionary.Choice(title=f"⚡ {w.name}: {w.description}", value=w)
        for w in workloads
    ]
    choices.append(questionary.Choice(title="⬅️ Back", value=None))

    selected = questionary.select("Select AI Workload to Launch:", choices=choices, style=QUESTIONARY_STYLE).ask()
    if not selected:
        return

    nb = pick_notebook(client, "Select target Blackwell pod:")
    if not nb:
        return

    params = {}
    for p_name, p_spec in selected.parameters.items():
        desc = p_spec.get("description", p_name)
        val = questionary.text(f"Parameter '{p_name}' ({desc}):", style=QUESTIONARY_STYLE).ask()
        if val:
            params[p_name] = val

    with console.status(f"[bold green]Submitting workload '{selected.name}'...[/bold green]"):
        try:
            cmd = selected.command_builder(params) if selected.command_builder else "echo 'No command'"
            jm = JobManager()
            job = jm.submit_job(notebook_id=nb["id"], command=cmd, name=selected.name)
            render_success_card("Workload Running!", f"Launched {selected.name} on {nb['id']}.\nJob ID: {job['id']}")
        except Exception as e:
            render_error_card("Workload Launch Failed", str(e))
    questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()


def action_terminal_chat(notebook_id: str) -> None:
    """Interactive chat with deployed LLM."""
    from molab_cli.chat import run_terminal_chat
    run_terminal_chat(notebook_id=notebook_id)



def action_localhost_bridge(notebook_id: str) -> None:
    """Launch local HTTP proxy exposing localhost:8000/v1."""
    session = SandboxSession(notebook_id)
    port = 8000
    console.print(Panel(
        f"🚀 Localhost Bridge Active on [bold cyan]http://localhost:{port}/v1[/bold cyan]\n"
        f"Forwarding to remote port 8000 on pod {notebook_id}.\nPress Ctrl+C to stop.",
        title="[bold green]MoLab Port Forward Bridge[/bold green]",
        border_style="green",
    ))
    forwarder = LocalHttpForwarder(session, port=port)
    try:
        forwarder.start()
    except KeyboardInterrupt:
        console.print("\n[dim]Bridge stopped cleanly.[/dim]")


def display_gpu_telemetry(notebook_id: str) -> None:
    """Render GPU and hardware telemetry."""
    session = SandboxSession(notebook_id)
    with console.status("[bold green]Querying NVIDIA Blackwell GPU telemetry...[/bold green]"):
        try:
            info = session.get_gpu_telemetry()
        except Exception as e:
            render_error_card("Telemetry Error", str(e))
            return

    table = Table(
        title="[bold #76b900]⚡ NVIDIA RTX PRO 6000 Blackwell Server Edition Telemetry[/bold #76b900]",
        border_style="#76b900",
        expand=True,
    )
    table.add_column("Property", style="bold white", width=25)
    table.add_column("Value / Metric", style="bold cyan")

    table.add_row("GPU Device", str(info.get("device_name", "NVIDIA RTX PRO 6000 Blackwell")))
    table.add_row("Total VRAM", f"{info.get('total_vram_gb', 94.97)} GB GDDR7")
    table.add_row("Allocated VRAM", f"{info.get('allocated_vram_gb', 0.0)} GB")
    table.add_row("CUDA Runtime", str(info.get("cuda_version", "13.0")))
    table.add_row("PyTorch Version", str(info.get("torch_version", "2.11.0")))
    table.add_row("Host System RAM", f"{info.get('host_ram_gb', 160.0)} GiB RAM")
    table.add_row("Host CPU Cores", f"{info.get('cpu_cores', 20)} vCPUs")

    console.print(table)
    questionary.text("Press Enter to return...", style=QUESTIONARY_STYLE).ask()


def action_file_transfer(client: MoLabClient) -> None:
    """Interactive file transfer hub."""
    nb = pick_notebook(client, "Select pod for file transfer:")
    if not nb:
        return
    nb_id = nb["id"]

    choice = questionary.select(
        "File Transfer Action:",
        choices=[
            "⬆️ Push Local File to Cloud Pod",
            "⬇️ Pull Cloud File to Local Storage",
            "⬅️ Back",
        ],
        style=QUESTIONARY_STYLE,
    ).ask()

    session = SandboxSession(nb_id, client=client)

    if choice and "Push" in choice:
        local_p = questionary.text("Enter local file path to upload:", style=QUESTIONARY_STYLE).ask()
        if not local_p or not os.path.exists(os.path.expanduser(local_p)):
            console.print("[red]Local file does not exist.[/red]")
            return
        remote_p = questionary.text("Enter remote destination path (default: /workspace/<filename>):", style=QUESTIONARY_STYLE).ask()
        with console.status("[bold cyan]Uploading file to cloud pod...[/bold cyan]"):
            try:
                dest, sz = session.push_file(local_p, remote_p if remote_p and remote_p.strip() else None)
                render_success_card("Upload Complete", f"Uploaded {sz:,} bytes to: [bold white]{dest}[/bold white]")
            except Exception as e:
                render_error_card("Upload Failed", str(e))

    elif choice and "Pull" in choice:
        remote_p = questionary.text("Enter remote file path in pod (e.g. /workspace/output.mp4):", style=QUESTIONARY_STYLE).ask()
        if not remote_p:
            return
        local_p = questionary.text("Enter local destination path (default: current directory):", style=QUESTIONARY_STYLE).ask()
        with console.status("[bold cyan]Downloading file from cloud pod...[/bold cyan]"):
            try:
                dest, sz = session.pull_file(remote_p, local_p if local_p and local_p.strip() else None)
                render_success_card("Download Complete", f"Downloaded {sz:,} bytes to: [bold white]{dest}[/bold white]")
            except Exception as e:
                render_error_card("Download Failed", str(e))


def action_ai_studio(client: MoLabClient) -> None:
    """Dedicated Blackwell AI Model Studio & Control Center submenu."""
    while True:
        choice = questionary.select(
            "🤖 AI Model Studio & Autonomous Agent Hub:",
            choices=[
                "🚀 1-Click Deploy Production Model (Qwen 32B / DeepSeek R1 / Llama 70B)",
                "💬 Terminal Coding Agent (Autonomous Tool Calling Loop)",
                "🌐 Start Unified Blackwell AI Bridge (Hermes on 8000 & Claude on 8082)",
                "📊 Live Inference Telemetry Profiler (Prefix Caching & GPU Thermals)",
                "🌍 Share Model Endpoint (Zero-Config Cloudflare Public Tunnel)",
                "🔑 Manage Gateway Virtual API Keys (RPM/TPM Limits)",
                "📈 Real-Time Gateway Analytics & Audit Logs",
                "🩺 Check Model Health & VRAM Status",
                "⬅️ Back to Main Menu",
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if not choice or "Back" in choice:
            break

        try:
            from molab_cli.sandbox import resolve_target_notebook
            active_id = resolve_target_notebook(client, require_running=True)
        except Exception:
            nb = pick_notebook(client, "Select the notebook running your model server:")
            if not nb:
                continue
            active_id = nb["id"]

        if "1-Click Deploy" in choice:
            model_alias = questionary.select(
                "Select model profile to deploy on Blackwell 96GB:",
                choices=[
                    "qwen-32b    (Qwen 2.5 32B Instruct Abliterated - 32K Context)",
                    "coder-32b   (Qwen 2.5 Coder 32B Instruct - 64K Context Workhorse)",
                    "r1-32b      (DeepSeek R1 Distill Qwen 32B - Reasoning CoT)",
                    "llama-70b   (Llama 3.3 70B Instruct FP8 - High Precision)",
                ],
                style=QUESTIONARY_STYLE,
            ).ask()
            if model_alias:
                alias = model_alias.split()[0]
                from molab_cli.deploy import deploy_model_on_pod
                with console.status(f"[bold green]Deploying {alias} to Blackwell pod...[/bold green]"):
                    try:
                        res = deploy_model_on_pod(model_alias=alias, pod_id=active_id)
                        render_success_card("Model Deployed!", f"Model: {res['model_id']}\nEndpoint: {res['local_url']}\nAPI Key: {res['api_key']}")
                    except Exception as e:
                        render_error_card("Deployment Failed", str(e))
                questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Terminal Coding Agent" in choice:
            action_terminal_chat(active_id)

        elif "Start Unified Blackwell AI Bridge" in choice:
            action_localhost_bridge(active_id)

        elif "Live Inference Telemetry" in choice:
            from molab_cli.perf import query_pod_performance, render_performance_dashboard
            with console.status("[bold cyan]Querying inference telemetry from Blackwell pod...[/bold cyan]"):
                try:
                    data = query_pod_performance(notebook_id=active_id)
                    render_performance_dashboard(data)
                except Exception as e:
                    render_error_card("Telemetry Error", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Share Model Endpoint" in choice:
            from molab_cli.tunnel import TunnelManager, render_credentials
            mgr = TunnelManager()
            st = mgr.get_status()
            if st.get("active"):
                render_credentials(st)
                stop_it = questionary.confirm("Tunnel is running. Stop public sharing?", default=False, style=QUESTIONARY_STYLE).ask()
                if stop_it:
                    mgr.stop_tunnel()
                    render_success_card("Tunnel Stopped", "Public tunnel has been terminated.")
            else:
                with console.status("[bold cyan]Starting Cloudflare Quick Tunnel...[/bold cyan]"):
                    try:
                        tun = mgr.start_quick_tunnel(local_port=8000)
                        render_credentials(tun)
                    except Exception as e:
                        render_error_card("Tunnel Failed", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Manage Gateway Virtual API Keys" in choice:
            from molab_cli.gateway_db import GatewayDB
            db = GatewayDB()
            keys = db.list_keys()
            console.print(f"[bold cyan]Active Gateway Virtual Keys ({len(keys)}):[/bold cyan]")
            for k in keys:
                console.print(f"  • [bold white]{k['key']}[/bold white] | Name: {k['name']} | RPM: {k['rpm_limit']} | Status: {k['status']}")
            sub = questionary.select("Key Action:", choices=["➕ Issue New Virtual Key", "🗑️ Revoke Key", "⬅️ Back"], style=QUESTIONARY_STYLE).ask()
            if sub and "Issue New" in sub:
                kname = questionary.text("Key Name / Tenant (e.g. cursor-ide):", default="dev-client", style=QUESTIONARY_STYLE).ask()
                if kname:
                    new_k = db.create_key(name=kname)
                    render_success_card("Virtual Key Created!", f"Key: {new_k['key']}\nTenant: {kname}")
            elif sub and "Revoke" in sub:
                kto_rev = questionary.text("Enter API key to revoke:", style=QUESTIONARY_STYLE).ask()
                if kto_rev:
                    db.revoke_key(kto_rev.strip())
                    render_success_card("Key Revoked", f"Key {kto_rev} revoked.")
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Real-Time Gateway Analytics" in choice:
            from molab_cli.gateway_db import GatewayDB
            db = GatewayDB()
            stats = db.get_stats()
            t = Table(title="AI Gateway Usage Analytics", box=box.ROUNDED)
            t.add_column("Metric", style="cyan")
            t.add_column("Value", style="green")
            t.add_row("Total Requests", str(stats.get("total_requests", 0)))
            t.add_row("Total Prompt Tokens", f"{stats.get('total_prompt_tokens', 0):,}")
            t.add_row("Total Completion Tokens", f"{stats.get('total_completion_tokens', 0):,}")
            t.add_row("Active Virtual Keys", str(stats.get("active_keys", 0)))
            console.print(t)
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()

        elif "Check Model Health" in choice:
            sess = SandboxSession(active_id, client=client)
            with console.status("[bold cyan]Pinging model server inside pod...[/bold cyan]"):
                try:
                    res = sess.execute_command("curl -s http://127.0.0.1:8000/health", timeout=10.0)
                    console.print(Panel(res, title="Health Check Output", border_style="green"))
                except Exception as e:
                    render_error_card("Model Health Error", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()


def action_create_notebook(client: MoLabClient) -> None:
    """Interactive notebook creation wizard."""
    title = questionary.text("Enter notebook title:", default="Blackwell-Workspace", style=QUESTIONARY_STYLE).ask()
    if not title:
        return
    hw = questionary.select(
        "Select compute hardware:",
        choices=[
            "⚡ NVIDIA RTX PRO 6000 Blackwell (96GB VRAM)",
            "🖥️ CPU instance (4 vCPU, 32GB RAM)",
        ],
        style=QUESTIONARY_STYLE,
    ).ask()
    gpu = "rtxp6000" if "Blackwell" in hw else "none"

    with console.status("[bold green]Creating and provisioning notebook container...[/bold green]"):
        try:
            nb = client.create_notebook(title=title, gpu=gpu)
            render_success_card("Notebook Created!", f"ID: {nb['id']}\nHardware: {hw}")
        except Exception as e:
            render_error_card("Creation Failed", str(e))
    questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()


def action_account_settings() -> None:
    """Manage auth credentials and session."""
    auth_info = inspect_auth_status()
    panel_text = (
        f"Email:        [bold white]{auth_info.get('user_email', 'Unknown')}[/bold white]\n"
        f"User ID:      [dim]{auth_info.get('user_id', 'Unknown')}[/dim]\n"
        f"Session ID:   [dim]{auth_info.get('session_id', 'Unknown')}[/dim]\n"
        f"Organization: [cyan]{auth_info.get('org_slug', 'Personal')}[/cyan]\n"
        f"Status:       {'[bold green]● Active (Auto-Minting RS256 JWTs)[/bold green]' if auth_info.get('authenticated') else '[bold red]○ Inactive[/bold red]'}"
    )
    console.print(Panel(panel_text, title="🔑 Account & Authentication", border_style="cyan"))

    choice = questionary.select(
        "Account Actions:",
        choices=[
            "🔄 Re-authenticate / Switch Account",
            "🚪 Clear Stored Credentials (Log Out)",
            "⬅️ Back",
        ],
        style=QUESTIONARY_STYLE,
    ).ask()

    if choice and "Re-authenticate" in choice:
        run_onboarding_wizard()
    elif choice and "Clear" in choice:
        confirm = questionary.confirm("Are you sure you want to log out and clear stored tokens?", default=False, style=QUESTIONARY_STYLE).ask()
        if confirm:
            save_config({})
            render_success_card("Logged Out", "Stored credentials have been cleared from ~/.config/molab/config.json.")


def action_show_help() -> None:
    """Display quick reference cheatsheet."""
    guide = (
        f"### ⚡ MoLab CLI Cheat Sheet (v{__version__})\n\n"
        "| Command | Description |\n"
        "|---|---|\n"
        "| `molab` / `molab ui` | Launch this interactive TUI Control Center dashboard |\n"
        "| `molab deploy <alias>` | 1-Click production model deployment on Blackwell 96GB |\n"
        "| `molab perf` | Live Prefix Cache Hit Rate %, TTFT, and Blackwell thermals |\n"
        "| `molab chat` | Autonomous terminal coding agent loop with /undo |\n"
        "| `molab share` / `public` | Expose endpoint to external clients via Cloudflare tunnel |\n"
        "| `molab free` | Audit Blackwell pods and recommend free idle pod |\n"
        "| `molab vault <pack|unpack>` | 100% on-MoLab permanent storage vault (0 local bytes) |\n"
        "| `molab permanent <id>` | 1-Click 24/7 infinite machine (defeats 30m idle reaper) |\n"
        "| `molab forward <id>` | Bridge localhost:8000 to remote model server |\n"
        "| `molab keys <create|list>` | Issue virtual API keys with RPM/TPM limits |\n"
        "| `molab stats` | Real-time AI Gateway token analytics & audit logs |\n"
        "| `molab job submit '<cmd>'` | Submit background job tracked in local SQLite |\n"
        "| `molab batch run <file>` | Multi-pod DAG pipeline orchestration |\n"
        "| `molab push/pull <id>` | High-speed native HTTP/2 streaming transfers |\n"
    )
    console.print(Panel(Markdown(guide), title="📖 Quick Reference & Documentation", border_style="cyan"))
    questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()


_FREE_POD_CACHE: Dict[str, Any] = {"pod_id": None, "timestamp": 0.0}


def _get_cached_free_pod(client: MoLabClient, running_pods: Dict[str, str]) -> Optional[str]:
    """Retrieve free pod recommendation with 60-second cache to keep main menu instantaneous."""
    if not running_pods:
        return None
    now = time.time()
    cached_id = _FREE_POD_CACHE.get("pod_id")
    if now - _FREE_POD_CACHE.get("timestamp", 0.0) < 60.0 and cached_id in running_pods:
        return cached_id

    # If only 1 pod is running, use it directly without blocking the event loop
    if len(running_pods) == 1:
        single_id = list(running_pods.keys())[0]
        _FREE_POD_CACHE["pod_id"] = single_id
        _FREE_POD_CACHE["timestamp"] = now
        return single_id

    candidate = list(running_pods.keys())[0]
    _FREE_POD_CACHE["pod_id"] = candidate
    _FREE_POD_CACHE["timestamp"] = now
    return candidate


def start_interactive_tui() -> None:
    """Main interactive Control Center event loop."""
    if not ensure_authenticated():
        return

    client = MoLabClient()

    while True:
        try:
            auth_info = inspect_auth_status()
            running_pods = client.list_running_sandboxes()
            running_count = len(running_pods)

            # Retrieve fast non-blocking free pod hint
            free_pod_id = _get_cached_free_pod(client, running_pods)

            render_banner()
            render_status_bar(auth_info, running_pods_count=running_count, recommended_free_pod=free_pod_id)

            choices = [
                "📋 Browse & Manage Notebooks",
                "🚀 1-Click Launch Blackwell Pod",
                "🎨 MoLab Community Gallery (111+ Recipes)",
                "🛡️ 100% On-MoLab Vault & Permanence",
                "⚡ Background Jobs & Batch DAGs",
                "🤖 AI Model Studio (Gemma 3 27B)",
                "🚀 Pre-Configured AI Workloads",
                "📊 Real-time Hardware & GPU Telemetry",
                "💻 Open Cloud Root Terminal (Shell)",
                "🌐 Launch Web Control Center (Browser)",
                "📂 Cloud File Transfer (Push / Pull)",
                "📦 Python Package Manager",
                "🔑 Account & Authentication",
                "❓ Quick Guide & Documentation",
                "🚪 Exit",
            ]

            action = questionary.select(
                "Select an action:",
                choices=choices,
                style=QUESTIONARY_STYLE,
            ).ask()

            if not action or "Exit" in action:
                console.print("[dim]Goodbye![/dim]")
                sys.exit(0)

            if "Browse & Manage Notebooks" in action:
                action_browse_notebooks(client)
            elif "1-Click Launch Blackwell Pod" in action:
                action_create_notebook(client)
            elif "Community Gallery" in action:
                action_community_gallery(client)
            elif "Vault & Permanence" in action:
                action_vault_permanence(client)
            elif "Background Jobs" in action:
                action_jobs_and_batches(client)
            elif "AI Model Studio" in action:
                action_ai_studio(client)
            elif "Pre-Configured AI Workloads" in action:
                action_workloads(client)
            elif "Real-time Hardware & GPU Telemetry" in action:
                nb = pick_notebook(client, "Select pod for GPU telemetry:")
                if nb:
                    display_gpu_telemetry(nb["id"])
            elif "Open Cloud Root Terminal" in action:
                nb = pick_notebook(client, "Select pod to open shell:")
                if nb:
                    sess = SandboxSession(nb["id"], client=client)
                    sess.interactive_shell()
            elif "Launch Web Control Center" in action:
                from molab_cli.web import start_web_server
                start_web_server(port=8080, open_browser=True)
            elif "Cloud File Transfer" in action:
                action_file_transfer(client)
            elif "Python Package Manager" in action:
                nb = pick_notebook(client, "Select pod to install packages:")
                if nb:
                    pkgs = questionary.text("Enter packages to install (e.g. transformers torch):", style=QUESTIONARY_STYLE).ask()
                    if pkgs and pkgs.strip():
                        sess = SandboxSession(nb["id"], client=client)
                        with console.status(f"[bold cyan]Installing {pkgs}...[/bold cyan]"):
                            try:
                                out = sess.execute_command(f"uv pip install {pkgs.strip()}", timeout=120.0)
                                console.print(Panel(out, title="Output", border_style="cyan"))
                            except Exception as e:
                                render_error_card("Install Error", str(e))
                        questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()
            elif "Account & Authentication" in action:
                action_account_settings()
            elif "Quick Guide" in action:
                action_show_help()

        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            continue
        except Exception as e:
            render_error_card("Unexpected Error", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()
