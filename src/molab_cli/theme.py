"""
Theme, styling, ASCII banners, and Rich console utilities for molab-cli.
"""

from typing import Any, Dict, Optional

from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Shared Rich Console
console = Console()

# Brand & Palette Constants (OLED Dark Mode)
COLOR_BG = "#0F172A"          # Deep obsidian slate
COLOR_PRIMARY = "#06B6D4"     # Electric Cyan
COLOR_ACCENT = "#22C55E"      # Emerald / Run Green
COLOR_BLACKWELL = "#76B900"   # NVIDIA Lime Green
COLOR_WARNING = "#F59E0B"     # Amber
COLOR_DANGER = "#EF4444"      # Crimson Rose
COLOR_MUTED = "#64748B"       # Slate Gray
COLOR_CARD = "#1E293B"        # Midnight Blue

# Questionary modern prompt style
QUESTIONARY_STYLE = Style([
    ("qmark", "fg:#06b6d4 bold"),          # Cyan question mark
    ("question", "bold fg:#f8fafc"),        # White bold question text
    ("answer", "fg:#22c55e bold"),          # Green answer
    ("pointer", "fg:#06b6d4 bold"),         # Cyan pointer '❯'
    ("highlighted", "fg:#06b6d4 bold"),     # Cyan highlighted choice
    ("selected", "fg:#22c55e"),             # Green selected checkbox
    ("separator", "fg:#475569"),            # Dim separator
    ("instruction", "fg:#64748b italic"),   # Muted instructions
    ("text", "fg:#f8fafc"),                 # Normal text
    ("disabled", "fg:#475569 italic"),
])

# ASCII Banner
BANNER_ASCII = r"""
 ███╗   ███╗ ██████╗ ██╗      █████╗ ██████╗ 
 ████╗ ████║██╔═══██╗██║     ██╔══██╗██╔══██╗
 ██╔████╔██║██║   ██║██║     ███████║██████╔╝
 ██║╚██╔╝██║██║   ██║██║     ██╔══██║██╔══██╗
 ██║ ╚═╝ ██║╚██████╔╝███████╗██║  ██║██████╔╝
 ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝ 
"""


def render_banner(subtitle: str = "Cloud Notebooks & NVIDIA Blackwell Server Hub") -> None:
    """Print the stylized MoLab header banner."""
    title_text = Text(BANNER_ASCII, style="bold cyan")
    sub_text = Text(f"\n   ⚡ {subtitle}  •  v1.0.0", style="dim cyan")
    content = Text.assemble(title_text, sub_text)
    console.print(Panel(
        content,
        border_style="cyan",
        padding=(0, 2),
    ))


def render_status_bar(
    auth_info: Dict[str, Any],
    running_pods_count: int = 0,
    running_count: Optional[int] = None,
    **kwargs: Any,
) -> None:
    """Render a modern compact status bar showing account and pod status."""
    if running_count is not None:
        running_pods_count = running_count
    is_auth = auth_info.get("authenticated", False)
    email = auth_info.get("user_email") or "Not Logged In"

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="right", ratio=1)

    auth_pill = (
        f"[bold green]●[/bold green] [bold white]{email}[/bold white]"
        if is_auth
        else "[bold red]○ Unauthenticated[/bold red]"
    )

    pods_pill = (
        f"[bold bright_green]⚡ {running_pods_count} Pod Running[/bold bright_green] [dim](Blackwell 96GB)[/dim]"
        if running_pods_count > 0
        else "[dim]0 Pods Running[/dim]"
    )

    hw_pill = "[bold #76b900]NVIDIA RTX PRO 6000[/bold #76b900] [dim](sm_120)[/dim]"

    grid.add_row(auth_pill, pods_pill, hw_pill)
    console.print(Panel(grid, border_style="dim cyan", padding=(0, 1)))


def render_error_card(title: str, message: str, hint: Optional[str] = None) -> None:
    """Render an eye-catching error card with suggestions."""
    body = Text()
    body.append(f"{message}\n", style="white")
    if hint:
        body.append(f"\n💡 Suggested Action:\n", style="bold yellow")
        body.append(f"  {hint}\n", style="dim yellow")

    console.print(Panel(
        body,
        title=f"[bold red]❌ {title}[/bold red]",
        border_style="red",
        padding=(1, 2),
    ))


def render_success_card(title: str, message: str) -> None:
    """Render a celebratory success card."""
    console.print(Panel(
        Text(message, style="white"),
        title=f"[bold green]✨ {title}[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))


def render_warning_card(title: str, message: str) -> None:
    """Render a warning card."""
    console.print(Panel(
        Text(message, style="white"),
        title=f"[bold yellow]⚠️ {title}[/bold yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))


def render_info_card(title: str, message: str) -> None:
    """Render an informational card."""
    console.print(Panel(
        Text(message, style="white"),
        title=f"[bold cyan]ℹ️ {title}[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
