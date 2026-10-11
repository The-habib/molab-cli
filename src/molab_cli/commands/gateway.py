"""
API bridge, tunnels, ingress forwarding, telemetry, and credentials commands.
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


@click.command("forward")
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



@click.group("bridge")
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
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*molab_cli.bridge*' -and $_.ProcessId -ne $PID } | Stop-Process -Force"],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
        else:
            subprocess.run(["pkill", "-f", "molab_cli.bridge"], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    console.print("[yellow]✓ Blackwell bridge stopped.[/yellow]")



@click.command("share")
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



@click.command("public")
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



@click.command("credentials")
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



@click.command("creds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output active credentials as JSON")
@click.option("-e", "--export", is_flag=True, help="Print shell export statements for instant environment setup")
@click.pass_context
def cmd_creds(ctx, as_json: bool, export: bool):
    """Fast shortcut for 'molab credentials'."""
    ctx.invoke(cmd_credentials, as_json=as_json, export=export)



@click.group("keys")
def keys_group():
    """Manage AI Gateway virtual API keys, quotas, and access governance."""
    pass



@keys_group.command("create")
@click.argument("name", required=False)
@click.option("--name", "name_opt", default=None, help="Name or label for the virtual key")
@click.option("--rpm", default=60, help="Requests Per Minute rate limit (default: 60)")
@click.option("--tpm", default=60000, help="Tokens Per Minute rate limit (default: 60000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_keys_create(name: Optional[str], name_opt: Optional[str], rpm: int, tpm: int, as_json: bool):
    """Create a new virtual API key with custom rate and token limits."""
    from molab_cli.gateway_db import default_gateway_db
    target_name = name or name_opt or "Virtual Key"
    raw_key, info = default_gateway_db.create_key(name=target_name, rpm_limit=rpm, tpm_limit=tpm)
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



@keys_group.command("delete")
@click.argument("key_id")
@click.pass_context
def cmd_keys_delete(ctx, key_id: str):
    """Alias for revoke: deactivate a virtual API key immediately."""
    ctx.invoke(cmd_keys_revoke, key_id=key_id)



@click.command("stats")
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



@click.command("analytics")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output metrics as JSON")
@click.option("--limit", default=10, help="Number of recent request logs to display")
@click.pass_context
def cmd_analytics(ctx, as_json: bool, limit: int):
    """Alias for 'molab stats' — display AI Gateway usage metrics."""
    ctx.invoke(cmd_stats, as_json=as_json, limit=limit)



@click.command("perf")
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



@click.command("deploy")
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



@click.group("serve")
def serve_group():
    """Manage OpenAI-compatible model servers and application endpoints."""
    pass



@serve_group.command("status")
@click.argument("notebook_id", required=False)
@click.option("--port", default=8000, help="Port to inspect (default: 8000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output status as JSON")
def cmd_serve_status(notebook_id: Optional[str], port: int, as_json: bool):
    """Verify application-level health and port binding for model server."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Service Target Resolution Failed", as_json=as_json)
        return

    from molab_cli.services import ServiceManager
    session = SandboxSession(target_nb, client=client)
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
        title=f"Service on {target_nb}:{port}",
        border_style="cyan",
    ))



@serve_group.command("stop")
@click.argument("notebook_id", required=False)
@click.option("--port", default=8000, help="Port to stop (default: 8000)")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output result as JSON")
def cmd_serve_stop(notebook_id: Optional[str], port: int, as_json: bool):
    """Gracefully terminate model service running on pod."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Service Target Resolution Failed", as_json=as_json)
        return

    from molab_cli.services import ServiceManager
    session = SandboxSession(target_nb, client=client)
    sm = ServiceManager(session)
    ok = sm.stop_service(port=port)
    if as_json:
        print(json.dumps({"success": ok, "port": port}, indent=2))
        return
    console.print(f"[bold green]Service on port {port} stopped.[/bold green]")



@serve_group.command("logs")
@click.argument("notebook_id", required=False)
@click.option("--tail", default=50, help="Number of lines to read")
def cmd_serve_logs(notebook_id: Optional[str], tail: int):
    """Read recent service logs from pod."""
    client = MoLabClient()
    try:
        target_nb = resolve_target_notebook(client, notebook_id, require_running=True)
    except Exception as e:
        handle_cli_error(e, title="Service Target Resolution Failed")
        return

    from molab_cli.services import ServiceManager
    session = SandboxSession(target_nb, client=client)
    sm = ServiceManager(session)
    logs = sm.get_service_logs(tail_lines=tail)
    console.print(logs)



GATEWAY_COMMANDS = [
    cmd_forward,
    bridge_group,
    cmd_share,
    cmd_public,
    cmd_credentials,
    cmd_creds,
    keys_group,
    cmd_stats,
    cmd_analytics,
    cmd_perf,
    cmd_deploy,
    serve_group,
]
