"""
Interactive Control Center & TUI Dashboard for MoLab CLI.
Provides zero-typing navigation, pod management, and AI model controls.
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional

import questionary
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from molab_cli.auth import inspect_auth_status, save_and_verify_auth
from molab_cli.client import MoLabClient
from molab_cli.config import save_config
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


def pick_notebook(client: MoLabClient, title: str = "Select a notebook:") -> Optional[Dict[str, Any]]:
    """Interactive notebook selector."""
    with console.status("[bold cyan]Fetching notebooks from MoLab...[/bold cyan]"):
        try:
            notebooks = client.list_notebooks()
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
        is_running = nb.get("running", False)

        hw_tag = "⚡ Blackwell 96GB" if "rtx" in gpu.lower() or "blackwell" in gpu.lower() else "CPU"
        status_tag = "🟢 RUNNING" if is_running else "⚪ STOPPED"

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
            except Exception as e:
                render_error_card("Error Fetching Notebooks", str(e))
                return

        # Render Table
        table = Table(
            title="[bold cyan]📓 Cloud Notebooks Workspace[/bold cyan]",
            border_style="cyan",
            expand=True,
        )
        table.add_column("Status", justify="center", width=12)
        table.add_column("Notebook Title", style="bold white", ratio=2)
        table.add_column("Compute Hardware", justify="center", width=22)
        table.add_column("ID", style="dim", width=26)

        for nb in notebooks:
            is_running = nb.get("running", False)
            gpu = nb.get("gpu", "")
            status_text = "[bold green]🟢 RUNNING[/bold green]" if is_running else "[dim]⚪ STOPPED[/dim]"
            hw_text = "[bold #76b900]⚡ Blackwell 96GB[/bold #76b900]" if "rtx" in gpu.lower() else "[cyan]4 vCPU / 32GB[/cyan]"
            table.add_row(status_text, nb.get("title", "Untitled"), hw_text, nb["id"])

        console.print(table)

        choices = [
            "🔍 Select Notebook for Actions (Terminal, AI, Compute, etc.)",
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
    """Action menu for a specific selected notebook."""
    nb_id = nb["id"]

    while True:
        # Re-inspect to get live data
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
        panel_content.append(f"Title:    ", style="dim")
        panel_content.append(f"{title}\n", style="bold white")
        panel_content.append(f"ID:       ", style="dim")
        panel_content.append(f"{nb_id}\n", style="cyan")
        panel_content.append(f"Compute:  ", style="dim")
        panel_content.append(f"{hw_label}\n", style="bold #76b900" if "rtx" in gpu.lower() else "white")
        panel_content.append(f"Sandbox:  ", style="dim")
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
            "⚡ Switch Compute (NVIDIA Blackwell 96GB ⟷ CPU)",
            "📊 Real-time GPU Telemetry & VRAM",
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

        if "Open Interactive Root Terminal" in choice:
            console.print(f"[bold cyan]Connecting to interactive bash on {title}...[/bold cyan]")
            try:
                session = SandboxSession(nb_id, client=client)
                session.interactive_shell()
            except Exception as e:
                render_error_card("Terminal Connection Error", str(e))

        elif "Chat with Deployed 27B LLM" in choice:
            action_terminal_chat(nb_id)

        elif "Start Localhost Bridge" in choice:
            action_localhost_bridge(nb_id)

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

        elif "Real-time GPU Telemetry" in choice:
            display_gpu_telemetry(nb_id)

        elif "View Python Code" in choice:
            with console.status("[bold cyan]Fetching code...[/bold cyan]"):
                try:
                    sess = SandboxSession(nb_id, client=client)
                    cfg, cells = sess.fetch_notebook_cells()
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
                        sess = SandboxSession(nb_id, client=client)
                        out = sess.execute_command(f"uv pip install {pkgs.strip()}", timeout=120.0)
                        console.print(Panel(out, title="Installation Output", border_style="cyan"))
                    except Exception as e:
                        render_error_card("Install Failed", str(e))

        elif "Stop / Shutdown Sandbox Pod" in choice:
            confirm = questionary.confirm("Stop this pod to free resources?", default=True, style=QUESTIONARY_STYLE).ask()
            if confirm:
                with console.status("[bold yellow]Stopping container pod...[/bold yellow]"):
                    try:
                        client.stop_notebook(nb_id)
                        render_success_card("Pod Stopped", f"Container for notebook {nb_id} has been shutdown.")
                    except Exception as e:
                        render_error_card("Stop Failed", str(e))

        elif "Duplicate / Clone Notebook" in choice:
            with console.status("[bold cyan]Cloning notebook...[/bold cyan]"):
                try:
                    new_id = client.duplicate_notebook(nb_id)
                    render_success_card("Notebook Duplicated", f"New notebook created: {new_id}")
                except Exception as e:
                    render_error_card("Clone Failed", str(e))

        elif "Rename Notebook" in choice:
            new_title = questionary.text("Enter new title:", default=title, style=QUESTIONARY_STYLE).ask()
            if new_title and new_title.strip() and new_title != title:
                with console.status("[bold cyan]Renaming notebook...[/bold cyan]"):
                    try:
                        client.rename_notebook(nb_id, new_title.strip())
                        render_success_card("Notebook Renamed", f"Title updated to: '{new_title.strip()}'")
                    except Exception as e:
                        render_error_card("Rename Failed", str(e))

        elif "Delete Notebook" in choice:
            confirm = questionary.confirm(f"Permanently delete '{title}'?", default=False, style=QUESTIONARY_STYLE).ask()
            if confirm:
                with console.status("[bold red]Deleting notebook...[/bold red]"):
                    try:
                        client.delete_notebook(nb_id)
                        render_success_card("Notebook Deleted", f"Notebook {nb_id} was removed.")
                        break
                    except Exception as e:
                        render_error_card("Delete Failed", str(e))


def action_create_notebook(client: MoLabClient) -> None:
    """1-click instant create notebook wizard."""
    title = questionary.text(
        "Enter notebook title:",
        default=f"blackwell-workspace-{int(time.time()) % 1000}",
        style=QUESTIONARY_STYLE,
    ).ask()
    if not title:
        return

    hw_choice = questionary.select(
        "Select compute hardware:",
        choices=[
            "⚡ NVIDIA RTX PRO 6000 Blackwell (96 GB VRAM) [Recommended]",
            "🖥️ CPU-only (4 vCPUs, 32 GB RAM)",
        ],
        style=QUESTIONARY_STYLE,
    ).ask()

    gpu = "rtxp6000" if "Blackwell" in hw_choice else ""

    with console.status("[bold green]Provisioning container pod on CoreWeave...[/bold green]"):
        try:
            nb_id = client.create_notebook(title=title, gpu=gpu)
            try:
                client.rename_notebook(nb_id, title)
            except Exception:
                pass

            render_success_card(
                "Notebook Created Successfully!",
                f"Notebook ID: [bold cyan]{nb_id}[/bold cyan]\n"
                f"Hardware:    [bold #76b900]{'NVIDIA RTX PRO 6000 Blackwell (96GB)' if gpu else 'CPU'}[/bold #76b900]\n"
                f"URL:         https://molab.marimo.io/notebooks/{nb_id}"
            )

            open_now = questionary.confirm("Would you like to open root bash shell now?", default=True, style=QUESTIONARY_STYLE).ask()
            if open_now:
                sess = SandboxSession(nb_id, client=client)
                sess.interactive_shell()
        except Exception as e:
            render_error_card("Creation Failed", str(e))


def action_terminal_chat(notebook_id: str) -> None:
    """Run interactive terminal chat with the deployed 27B model."""
    session = SandboxSession(notebook_id)
    console.print(Panel(
        "💬 [bold white]Gemma 3 27B IT Abliterated (Unrestricted BF16)[/bold white]\n"
        "⚡ Hardware: [bold #76b900]NVIDIA RTX PRO 6000 Blackwell (95GB VRAM)[/bold #76b900]\n"
        "💡 Type your prompt and press Enter. Type [bold red]exit[/bold red] or [bold red]/quit[/bold red] to end.",
        title="[bold green]Interactive Terminal Chat[/bold green]",
        border_style="green",
    ))

    history: List[Dict[str, str]] = []
    while True:
        try:
            user_input = questionary.text("You >", style=QUESTIONARY_STYLE).ask()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input or not user_input.strip():
            continue
        if user_input.strip().lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Chat session closed.[/dim]")
            break

        history.append({"role": "user", "content": user_input.strip()})
        with console.status("[bold green]Blackwell GPU generating tokens...[/bold green]"):
            try:
                res = session.chat_completion(history, max_tokens=512, temperature=0.7)
                reply = res.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                render_error_card("Generation Error", str(e))
                continue

        history.append({"role": "assistant", "content": reply})
        console.print(Panel(
            Markdown(reply),
            title="[bold green]Gemma 3 27B (Blackwell)[/bold green]",
            border_style="green",
        ))


def action_localhost_bridge(notebook_id: str) -> None:
    """Launch local HTTP proxy exposing localhost:8000/v1."""
    session = SandboxSession(notebook_id)
    port = 8000

    panel_text = Text()
    panel_text.append("🚀 Localhost Bridge Active!\n\n", style="bold green")
    panel_text.append(" • Local OpenAI Base URL: ", style="dim")
    panel_text.append(f"http://localhost:{port}/v1\n", style="bold cyan")
    panel_text.append(" • Chat Completions:     ", style="dim")
    panel_text.append(f"http://localhost:{port}/v1/chat/completions\n", style="cyan")
    panel_text.append(" • Model Health Check:   ", style="dim")
    panel_text.append(f"http://localhost:{port}/health\n", style="cyan")
    panel_text.append(" • Deployed Model:       ", style="dim")
    panel_text.append("gemma-3-27b-it-abliterated (Uncompressed BF16)\n", style="bold white")
    panel_text.append(" • Cloud Hardware:       ", style="dim")
    panel_text.append("NVIDIA RTX PRO 6000 Blackwell (95GB VRAM)\n\n", style="bold #76b900")
    panel_text.append("You can now connect Open WebUI, SillyTavern, Python SDK, or curl directly to localhost:8000.\n", style="white")
    panel_text.append("Press Ctrl+C in this terminal when you want to stop the bridge.", style="dim yellow")

    console.print(Panel(
        panel_text,
        title="[bold green]MoLab Localhost Bridge[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))

    forwarder = LocalHttpForwarder(session, port=port)
    try:
        forwarder.start()
    except KeyboardInterrupt:
        console.print("\n[dim]Bridge stopped cleanly.[/dim]")


def display_gpu_telemetry(notebook_id: str) -> None:
    """Render GPU telemetry dashboard."""
    session = SandboxSession(notebook_id)
    with console.status("[bold green]Querying NVIDIA Blackwell GPU telemetry...[/bold green]"):
        try:
            info = session.get_gpu_telemetry()
            server_health = session.execute_command("curl -s http://127.0.0.1:8000/health", timeout=10.0)
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
    table.add_row("Compute Capability", f"sm_{str(info.get('compute_capability', '12.0')).replace('.', '')} ({info.get('compute_capability', '12.0')})")
    table.add_row("Multiprocessors (SM)", f"{info.get('sm_count', 188)} SMs")
    table.add_row("CUDA Runtime", str(info.get("cuda_version", "13.0")))
    table.add_row("PyTorch Version", str(info.get("torch_version", "2.11.0")))
    table.add_row("Host System RAM", f"{info.get('host_ram_gb', 160.0)} GiB RAM")
    table.add_row("Host CPU Cores", f"{info.get('cpu_cores', 20)} vCPUs")

    console.print(table)

    if "allocated_gb" in server_health:
        console.print(Panel(
            f"[bold green]Live Model Server VRAM:[/bold green] {server_health}",
            title="Model Server Status",
            border_style="green",
        ))

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
        remote_p = questionary.text("Enter remote destination path (default: /marimo/<filename>):", style=QUESTIONARY_STYLE).ask()
        with console.status("[bold cyan]Uploading file to cloud pod...[/bold cyan]"):
            try:
                dest, sz = session.push_file(local_p, remote_p if remote_p.strip() else None)
                render_success_card("Upload Complete", f"Uploaded {sz:,} bytes to: [bold white]{dest}[/bold white]")
            except Exception as e:
                render_error_card("Upload Failed", str(e))

    elif choice and "Pull" in choice:
        remote_p = questionary.text("Enter remote file path in pod (e.g. /marimo/app.py):", style=QUESTIONARY_STYLE).ask()
        if not remote_p:
            return
        local_p = questionary.text("Enter local destination path (default: current directory):", style=QUESTIONARY_STYLE).ask()
        with console.status("[bold cyan]Downloading file from cloud pod...[/bold cyan]"):
            try:
                dest, sz = session.pull_file(remote_p, local_p if local_p.strip() else None)
                render_success_card("Download Complete", f"Downloaded {sz:,} bytes to: [bold white]{dest}[/bold white]")
            except Exception as e:
                render_error_card("Download Failed", str(e))


def action_ai_studio(client: MoLabClient) -> None:
    """Dedicated AI Model Studio submenu."""
    while True:
        choice = questionary.select(
            "🤖 AI Model Studio (27B Gemma 3 Abliterated):",
            choices=[
                "💬 Terminal Chat with Deployed 27B Model (Instant Conversation)",
                "🌐 Start Localhost Bridge (Expose http://localhost:8000/v1)",
                "🩺 Check Model Health & VRAM Status",
                "ℹ️ Model & Hardware Architecture Details",
                "⬅️ Back to Main Menu",
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if not choice or "Back" in choice:
            break

        # Pick active notebook (defaults to any running Blackwell notebook)
        running = client.list_running_sandboxes()
        active_id = list(running.keys())[0] if running else None
        if not active_id:
            nb = pick_notebook(client, "Select the notebook running your model server:")
            if not nb:
                continue
            active_id = nb["id"]

        if "Terminal Chat" in choice:
            action_terminal_chat(active_id)
        elif "Start Localhost Bridge" in choice:
            action_localhost_bridge(active_id)
        elif "Check Model Health" in choice:
            sess = SandboxSession(active_id, client=client)
            with console.status("[bold cyan]Pinging model server inside pod...[/bold cyan]"):
                try:
                    res = sess.execute_command("curl -s http://127.0.0.1:8000/health", timeout=10.0)
                    console.print(Panel(res, title="Health Check Output", border_style="green"))
                except Exception as e:
                    render_error_card("Model Health Error", str(e))
            questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()
        elif "Model & Hardware" in choice:
            details = (
                "• **Model**: `mlabonne/gemma-3-27b-it-abliterated` (27.2 Billion parameters)\n"
                "• **Weights**: Pure uncompressed BF16 safetensors (53.65 GB disk footprint)\n"
                "• **Safety**: Refusal vectors abliterated (unrestricted reasoning / zero refusals)\n"
                "• **VRAM Usage**: 51.10 GB allocated / 94.97 GB total (43.87 GB free for KV cache)\n"
                "• **Phone Storage**: **0 Bytes** (100% cloud container execution)\n"
                "• **GPU**: NVIDIA RTX PRO 6000 Blackwell Server Edition (`sm_120`)\n"
            )
            console.print(Panel(Markdown(details), title="AI Model Architecture", border_style="cyan"))
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
    """Display quick reference and cheatsheet."""
    guide = (
        "### ⚡ MoLab CLI Cheat Sheet\n\n"
        "| Command | Description |\n"
        "|---|---|\n"
        "| `molab` / `molabctl` | Launch this interactive Control Center dashboard |\n"
        "| `molab list` | List all cloud notebooks and active running pods |\n"
        "| `molab create [--blackwell]` | Provision a new sandbox pod (Blackwell 96GB GPU) |\n"
        "| `molab shell <id>` | Open an interactive root bash terminal in the pod |\n"
        "| `molab compute <id> --blackwell` | Switch notebook compute to NVIDIA Blackwell |\n"
        "| `molab chat <id>` | Terminal chat with deployed 27B unrestricted model |\n"
        "| `molab forward <id> [--port 8000]` | Bridge localhost:8000 to remote model server |\n"
        "| `molab gpu <id>` | Display real-time Blackwell GPU telemetry and VRAM |\n"
        "| `molab push <id> <local> <remote>` | Upload file or dataset directly into pod |\n"
        "| `molab pull <id> <remote> <local>` | Download file from pod to local storage |\n"
        "| `molab exec <id> '<cmd>'` | Execute a one-shot remote bash command |\n"
        "| `molab install <id> <packages>` | Install Python packages inside remote pod |\n"
        "| `molab stop <id>` | Stop/Shutdown a running container pod |\n"
        "| `molab status` | Check authentication & Clerk session validity |\n"
    )
    console.print(Panel(Markdown(guide), title="📖 Quick Reference & Documentation", border_style="cyan"))
    questionary.text("Press Enter to continue...", style=QUESTIONARY_STYLE).ask()


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

            render_banner()
            render_status_bar(auth_info, running_count=running_count)

            choices = [
                "📋 Browse & Manage Notebooks",
                "🚀 1-Click Launch Blackwell Pod",
                "💻 Open Cloud Root Terminal (Shell)",
                "🤖 AI Model Studio (Gemma 3 27B)",
                "⚡ Real-time GPU Telemetry",
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
            elif "Open Cloud Root Terminal" in action:
                nb = pick_notebook(client, "Select pod to open shell:")
                if nb:
                    sess = SandboxSession(nb["id"], client=client)
                    sess.interactive_shell()
            elif "AI Model Studio" in action:
                action_ai_studio(client)
            elif "Real-time GPU Telemetry" in action:
                nb = pick_notebook(client, "Select pod for GPU telemetry:")
                if nb:
                    display_gpu_telemetry(nb["id"])
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
