"""
Agent, UI, Web Dashboard, MCP, and Gallery commands.
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


@click.command("ui")
def cmd_ui():
    """Launch the interactive visual TUI Control Center dashboard."""
    from molab_cli.tui import start_interactive_tui
    start_interactive_tui()



@click.command("web")
@click.option("-p", "--port", default=8080, help="Port to bind web server (default: 8080)")
@click.option("--no-browser", is_flag=True, help="Do not open browser automatically")
def cmd_web(port: int, no_browser: bool):
    """Launch the modern browser-based Web Control Center dashboard."""
    from molab_cli.web import start_web_server
    start_web_server(port=port, open_browser=not no_browser)



@click.command("dashboard")
@click.option("-p", "--port", default=8080, help="Port to bind web server (default: 8080)")
@click.option("--no-browser", is_flag=True, help="Do not open browser automatically")
def cmd_dashboard(port: int, no_browser: bool):
    """Alias for 'molab web' — launch the modern browser-based Web Control Center."""
    from molab_cli.web import start_web_server
    start_web_server(port=port, open_browser=not no_browser)



@click.command("chat")
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



@click.command("mcp")
def cmd_mcp():
    """Start the Model Context Protocol (MCP) JSON-RPC 2.0 stdio server."""
    from molab_cli.mcp import run_mcp_server
    run_mcp_server()



@click.group("gallery")
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



AGENT_COMMANDS = [
    cmd_ui,
    cmd_web,
    cmd_dashboard,
    cmd_chat,
    cmd_mcp,
    gallery_group,
]
