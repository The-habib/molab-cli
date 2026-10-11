"""
File transfer, workspace synchronization, notebooks export, snapshots, and vaults.
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



@click.command("push")
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



@click.command("pull")
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



@click.command("cat")
@click.argument("notebook_id", required=False)
@click.option("--cell", type=int, help="Cell index (1-indexed)")
@click.pass_context
def cmd_cat(ctx, notebook_id: Optional[str], cell: Optional[int]):
    """Print Python code of notebook or specific cell."""
    if notebook_id and ("/" in notebook_id or "\\" in notebook_id or (not notebook_id.startswith("nb_") and "." in notebook_id)):
        console.print(f"[dim](Reading remote pod file '{notebook_id}' — for files, you can also use 'molab file cat <path>')[/dim]")
        ctx.invoke(cmd_file_cat, arg1=notebook_id, arg2=None)
        return

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
        info = session.client.inspect_notebook(target_nb)
        script = reconstruct_script(target_nb, info.get("title", ""), cells)
        syntax = Syntax(script, "python", theme="monokai", line_numbers=True)
        console.print(syntax)



@click.command("download")
@click.argument("notebook_id", required=False)
@click.option("-o", "--output", help="Output file path (.py)")
def cmd_download(notebook_id: Optional[str], output: Optional[str]):
    """Download notebook as a standalone runnable Python script."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Download Failed")
        return

    session = SandboxSession(target_nb, client=client)
    with console.status("[bold blue]Fetching notebook script...[/bold blue]"):
        try:
            cfg, cells = session.fetch_notebook_cells()
            info = session.client.inspect_notebook(target_nb)
        except Exception as e:
            console.print(f"[red]Error downloading notebook:[/red] {e}")
            return

    out_path = Path(output or f"{target_nb}.py")
    script = reconstruct_script(target_nb, info.get("title", ""), cells)
    out_path.write_text(script, encoding="utf-8")

    console.print(f"[green]✔ Saved {len(cells)} cells to {out_path.resolve()} ({out_path.stat().st_size} bytes)[/green]")
    console.print(f"[dim]Run locally: uvx marimo run {out_path.name}[/dim]")



@click.command("rename")
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



@click.command("clone")
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



@click.command("sync")
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



@click.group("export")
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



@click.group("file")
def file_group():
    """Manage remote files directly via high-speed native Marimo REST endpoints."""
    pass



def _resolve_file_args(arg1: Optional[str], arg2: Optional[str] = None) -> Tuple[str, str]:
    """Intelligently resolve (target_nb, path) for file commands."""
    client = MoLabClient()
    if arg1 is None:
        target_nb = resolve_target_notebook(client, None, require_running=True)
        return target_nb, arg2 or "."
    if arg2 is None:
        if arg1.startswith("nb_"):
            return arg1, "."
        target_nb = resolve_target_notebook(client, None, require_running=True)
        return target_nb, arg1
    return arg1, arg2


def _resolve_file_single_path(arg1: str, arg2: Optional[str] = None) -> Tuple[str, str]:
    """Intelligently resolve (target_nb, path) when a file path is mandatory."""
    client = MoLabClient()
    if arg2 is None:
        target_nb = resolve_target_notebook(client, None, require_running=True)
        return target_nb, arg1
    return arg1, arg2


@file_group.command("ls")
@click.argument("arg1", required=False, metavar="[NOTEBOOK_ID|PATH]")
@click.argument("arg2", required=False, metavar="[PATH]")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output file list as JSON")
def cmd_file_ls(arg1: Optional[str], arg2: Optional[str], as_json: bool):
    """List files and folders on pod via native REST endpoint."""
    try:
        notebook_id, path = _resolve_file_args(arg1, arg2)
    except Exception as e:
        handle_cli_error(e, title="File List Target Failed", as_json=as_json)
        return

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
@click.argument("arg1", metavar="[NOTEBOOK_ID|PATH]")
@click.argument("arg2", required=False, metavar="[PATH]")
def cmd_file_cat(arg1: str, arg2: Optional[str]):
    """View contents of a remote text file via native REST endpoint."""
    try:
        notebook_id, path = _resolve_file_single_path(arg1, arg2)
    except Exception as e:
        handle_cli_error(e, title="File Read Target Failed")
        return

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
@click.argument("arg1", metavar="[NOTEBOOK_ID|PATH]")
@click.argument("arg2", required=False, metavar="[PATH]")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output details as JSON")
def cmd_file_info(arg1: str, arg2: Optional[str], as_json: bool):
    """Fetch metadata and mime type of a file on the remote pod."""
    try:
        notebook_id, path = _resolve_file_single_path(arg1, arg2)
    except Exception as e:
        handle_cli_error(e, title="File Info Target Failed", as_json=as_json)
        return

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



@click.group("snapshot")
def snapshot_group():
    """Create, restore, and manage persistent workspace snapshots across pod resets."""
    pass



@snapshot_group.command("create")
@click.argument("notebook_id", required=False)
@click.option("--name", default=None, help="Descriptive snapshot checkpoint name")
@click.option("--remote-path", default="/workspace", help="Remote pod directory to archive")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output snapshot metadata as JSON")
def cmd_snapshot_create(notebook_id: Optional[str], name: Optional[str], remote_path: str, as_json: bool):
    """Create a compressed snapshot of pod workspace and stream to local persistent storage."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Snapshot Target Resolution Failed")
        return

    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    with console.status(f"[bold cyan]Archiving and streaming snapshot from {target_nb}:{remote_path}...[/bold cyan]"):
        try:
            snap = sm.create_snapshot(notebook_id=target_nb, name=name, remote_path=remote_path)
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
        f"Restore anytime: [bold]molab snapshot restore {target_nb} --snapshot-id {snap['id']}[/bold]",
        title="Workspace Snapshot",
        border_style="green",
    ))



@snapshot_group.command("restore")
@click.argument("notebook_id", required=False)
@click.option("--snapshot-id", default=None, help="Specific snapshot ID (default: latest snapshot)")
@click.option("--remote-path", default="/workspace", help="Destination pod directory to unpack into")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output restore summary as JSON")
def cmd_snapshot_restore(notebook_id: Optional[str], snapshot_id: Optional[str], remote_path: str, as_json: bool):
    """Restore a snapshot into the remote pod's /workspace directory."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Snapshot Target Resolution Failed")
        return

    from molab_cli.snapshots import SnapshotManager
    sm = SnapshotManager()
    with console.status(f"[bold cyan]Streaming snapshot archive into {target_nb}:{remote_path}...[/bold cyan]"):
        try:
            res = sm.restore_snapshot(notebook_id=target_nb, snapshot_id=snapshot_id, remote_path=remote_path)
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



@click.group("storage")
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



@click.group("vault")
def vault_group():
    """100% on-MoLab permanent storage vault (zero phone storage, zero external cloud)."""
    pass



@vault_group.command("pack")
@click.argument("notebook_id", required=False)
@click.option("--source-dir", default="/workspace", help="Pod directory to pack (default: /workspace)")
@click.option("--max-size", default=25.0, type=float, help="Max vault size in MB (default: 25.0)")
@click.option("--as-json", is_flag=True)
def cmd_vault_pack(notebook_id: Optional[str], source_dir: str, max_size: float, as_json: bool):
    """Compress pod workspace files directly into self-extracting cell in notebook (100% on MoLab)."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Vault Target Resolution Failed")
        return

    from molab_cli.vault import MoLabVault
    vault = MoLabVault(target_nb)
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
@click.argument("notebook_id", required=False)
@click.option("--target-dir", default="/workspace", help="Target pod directory (default: /workspace)")
@click.option("--overwrite/--no-overwrite", default=True, help="Overwrite conflicting files on unpack (default: True)")
@click.option("--as-json", is_flag=True)
def cmd_vault_unpack(notebook_id: Optional[str], target_dir: str, overwrite: bool, as_json: bool):
    """Extract files from in-notebook vault directly into pod workspace."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Vault Target Resolution Failed")
        return

    from molab_cli.vault import MoLabVault
    vault = MoLabVault(target_nb)
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
@click.argument("notebook_id", required=False)
@click.option("--as-json", is_flag=True)
def cmd_vault_inspect(notebook_id: Optional[str], as_json: bool):
    """Check if notebook contains a permanent in-notebook vault."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=False)
    except Exception as e:
        handle_cli_error(e, title="Vault Target Resolution Failed")
        return

    from molab_cli.vault import MoLabVault
    vault = MoLabVault(target_nb)
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
                    console.print(f"[yellow]No in-notebook vault found for {target_nb}[/yellow]")
        except Exception as e:
            console.print(f"[red]Failed to inspect vault:[/red] {e}")



FILES_COMMANDS = [
    cmd_push,
    cmd_pull,
    cmd_cat,
    cmd_download,
    cmd_rename,
    cmd_clone,
    cmd_sync,
    export_group,
    file_group,
    snapshot_group,
    storage_group,
    vault_group,
]
