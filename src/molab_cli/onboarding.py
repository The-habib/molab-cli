"""
Interactive first-time onboarding wizard and guided authentication setup.
"""

import sys
from typing import Optional

import questionary
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from molab_cli.auth import inspect_auth_status, save_and_verify_auth
from molab_cli.theme import (
    QUESTIONARY_STYLE,
    console,
    render_banner,
    render_error_card,
    render_success_card,
)


def display_cookie_guide() -> None:
    """Display an easy-to-follow guide on how to copy the Clerk __client cookie."""
    guide = Table.grid(padding=(0, 1))
    guide.add_column(style="bold cyan", width=8)
    guide.add_column(style="white")

    guide.add_row("Step 1:", "Open [bold underline cyan]https://molab.marimo.io[/bold underline cyan] in Chrome, Edge, or Firefox and sign in.")
    guide.add_row("Step 2:", "Press [bold yellow]F12[/bold yellow] (or right-click -> [bold yellow]Inspect[/bold yellow]) to open Developer Tools.")
    guide.add_row("Step 3:", "Click the [bold green]Application[/bold green] tab (or [bold green]Storage[/bold green] in Firefox).")
    guide.add_row("Step 4:", "Under [bold]Cookies[/bold] in the left sidebar, click [bold cyan]https://clerk.marimo.io[/bold cyan].")
    guide.add_row("Step 5:", "Double-click the value of [bold white]__client[/bold white] and copy it.")
    guide.add_row("Step 6:", "Paste it here. (You can also paste the full Cookie header if copied via DevTools!)")

    console.print(Panel(
        guide,
        title="[bold green]📖 How to Get Your Clerk Cookie (Takes 30 seconds)[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))


def run_onboarding_wizard() -> bool:
    """
    Run the full interactive first-time setup wizard.
    Returns True if successfully authenticated, False otherwise.
    """
    render_banner("Welcome & Setup Wizard")

    welcome_text = Text()
    welcome_text.append("MoLab brings enterprise cloud computing directly to your terminal:\n\n", style="white")
    welcome_text.append(" • ⚡ Free access to ", style="dim")
    welcome_text.append("NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM)\n", style="bold #76b900")
    welcome_text.append(" • 🚀 Interactive root bash terminal in ephemeral CoreWeave pods\n", style="dim")
    welcome_text.append(" • 🤖 1-click deployment & terminal chat with 27B uncompressed LLMs\n", style="dim")
    welcome_text.append(" • 🔒 Zero phone storage used — everything runs in the cloud\n\n", style="dim")
    welcome_text.append("To connect your MoLab account, we just need your Clerk master cookie.", style="cyan")

    console.print(Panel(welcome_text, title="[bold cyan]🚀 Welcome to MoLab CLI[/bold cyan]", border_style="cyan"))

    while True:
        action = questionary.select(
            "What would you like to do?",
            choices=[
                "🔑 Enter / Paste Clerk Cookie (Quick Setup)",
                "📖 Show Step-by-Step Browser Guide",
                "🚪 Exit Setup",
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if not action or "Exit" in action:
            console.print("[dim]Setup cancelled. Run 'molab' anytime to retry.[/dim]")
            return False

        if "Browser Guide" in action:
            display_cookie_guide()

        # Prompt for cookie
        raw_cookie = questionary.text(
            "Paste your __client cookie (or full cookie string):",
            style=QUESTIONARY_STYLE,
        ).ask()

        if not raw_cookie or not raw_cookie.strip():
            console.print("[yellow]No cookie entered. Please try again.[/yellow]")
            continue

        with console.status("[bold cyan]Connecting to Clerk Frontend API & validating session...[/bold cyan]"):
            try:
                res = save_and_verify_auth(raw_cookie.strip())
                user_email = res.get("user_email", "User")
                render_success_card(
                    "Connected Successfully!",
                    f"🎉 Authenticated as [bold white]{user_email}[/bold white]\n"
                    f"Session ID: [dim]{res.get('session_id')}[/dim]\n"
                    f"Config saved to: [dim]~/.config/molab/config.json[/dim]\n\n"
                    f"You're all set! MoLab CLI will automatically mint fresh tokens on demand."
                )
                return True
            except Exception as e:
                hint = getattr(e, "hint", "Please ensure you copied the active __client cookie.")
                render_error_card("Authentication Failed", str(e), hint=hint)
                retry = questionary.confirm("Would you like to try again?", default=True, style=QUESTIONARY_STYLE).ask()
                if not retry:
                    return False


def ensure_authenticated() -> bool:
    """
    Check if the user is authenticated; if not, automatically launch the onboarding wizard.
    """
    status = inspect_auth_status()
    if status.get("authenticated"):
        return True

    console.print("[bold yellow]⚠️ No active MoLab session detected.[/bold yellow]")
    return run_onboarding_wizard()
