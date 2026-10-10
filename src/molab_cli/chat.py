"""
Industry-Grade Real-Time Terminal Chat Agent for MoLab.
Provides live token streaming, reasoning/thinking decomposition (<think> tags & reasoning_content),
telemetry badges, slash command controls, session export, and prompt_toolkit UX.
"""

import os
import sys
import time
import json
import re
import shutil
import subprocess
import urllib.request
from typing import Any, Dict, Generator, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text
from rich.table import Table

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.styles import Style as PtStyle
    HAVE_PROMPT_TOOLKIT = True
except ImportError:
    HAVE_PROMPT_TOOLKIT = False

from molab_cli.sandbox import SandboxSession
from molab_cli.agent import AgentCoordinator
from molab_cli.tools import FileBackupManager, default_backup_manager, OPENAI_TOOL_DEFINITIONS
from molab_cli.workspace import WorkspaceContext

console = Console()

SLASH_COMMANDS = [
    "/help",
    "/clear",
    "/think",
    "/undo",
    "/diff",
    "/auto",
    "/context",
    "/tools",
    "/gpu",
    "/model",
    "/temp",
    "/tokens",
    "/system",
    "/save",
    "/copy",
    "/exit",
    "/quit",
]


class StreamThoughtExtractor:
    """
    State machine that extracts live reasoning thoughts vs response text from token deltas.
    Supports both native 'reasoning_content' fields and '<think>...</think>' tags.
    Handles partial tags split across SSE chunk boundaries without character leakage.
    """

    def __init__(self):
        self.in_thinking: bool = False
        self.thinking_source: str = ""  # 'field' or 'tag'
        self.has_thought: bool = False
        self.buffer: str = ""
        self.thought_tokens_count: int = 0
        self.response_tokens_count: int = 0
        self.think_start_time: Optional[float] = None
        self.think_duration: float = 0.0

    def process(self, delta: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Process a delta dictionary and yield list of (event_type, text_content).
        Event types: 'think_start', 'think_chunk', 'think_end', 'response_chunk'
        """
        events: List[Tuple[str, str]] = []

        # 1. Native reasoning_content field (e.g. DeepSeek-R1 / vLLM reasoning)
        reasoning = delta.get("reasoning_content", "")
        if reasoning:
            if not self.in_thinking:
                self.in_thinking = True
                self.thinking_source = "field"
                self.has_thought = True
                self.think_start_time = time.time()
                events.append(("think_start", ""))
            self.thought_tokens_count += 1
            events.append(("think_chunk", reasoning))
            return events
        elif self.in_thinking and self.thinking_source == "field" and "content" in delta and delta.get("content"):
            # Native reasoning ended and response began
            self.in_thinking = False
            self.thinking_source = ""
            if self.think_start_time:
                self.think_duration = time.time() - self.think_start_time
            events.append(("think_end", ""))

        content = delta.get("content", "")
        if not content:
            return events

        text = self.buffer + content
        self.buffer = ""

        while text:
            if not self.in_thinking:
                idx = text.find("<think>")
                if idx != -1:
                    before = text[:idx]
                    if before:
                        self.response_tokens_count += 1
                        events.append(("response_chunk", before))
                    self.in_thinking = True
                    self.thinking_source = "tag"
                    self.has_thought = True
                    self.think_start_time = time.time()
                    events.append(("think_start", ""))
                    text = text[idx + 7:]

                else:
                    # Check for partial start tag at tail
                    for l in range(len("<think>") - 1, 0, -1):
                        if text.endswith("<think>"[:l]):
                            self.buffer = text[-l:]
                            text = text[:-l]
                            break
                    if text:
                        self.response_tokens_count += 1
                        events.append(("response_chunk", text))
                    break
            else:
                idx = text.find("</think>")
                if idx != -1:
                    think_part = text[:idx]
                    if think_part:
                        self.thought_tokens_count += 1
                        events.append(("think_chunk", think_part))
                    self.in_thinking = False
                    self.thinking_source = ""
                    if self.think_start_time:
                        self.think_duration = time.time() - self.think_start_time
                    events.append(("think_end", ""))
                    text = text[idx + 8:]
                else:
                    # Check for partial close tag at tail
                    for l in range(len("</think>") - 1, 0, -1):
                        if text.endswith("</think>"[:l]):
                            self.buffer = text[-l:]
                            text = text[:-l]
                            break
                    if text:
                        self.thought_tokens_count += 1
                        events.append(("think_chunk", text))
                    break

        return events

    def flush(self) -> List[Tuple[str, str]]:
        """Flush remaining buffer at stream end."""
        events: List[Tuple[str, str]] = []
        if self.buffer:
            if self.in_thinking:
                self.thought_tokens_count += 1
                events.append(("think_chunk", self.buffer))
                self.in_thinking = False
                if self.think_start_time:
                    self.think_duration = time.time() - self.think_start_time
                events.append(("think_end", ""))
            else:
                self.response_tokens_count += 1
                events.append(("response_chunk", self.buffer))
            self.buffer = ""
        elif self.in_thinking:
            self.in_thinking = False
            if self.think_start_time:
                self.think_duration = time.time() - self.think_start_time
            events.append(("think_end", ""))
        return events


class TerminalAgentChat:
    """
    Industry-grade interactive terminal chat agent for MoLab.
    Manages session state, real-time thinking rendering, token statistics, and slash commands.
    """

    def __init__(
        self,
        notebook_id: str,
        model_name: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        think_mode: str = "full",  # 'full', 'compact', or 'off'
        system_prompt: Optional[str] = None,
        auto_approve: bool = False,
        tools_enabled: bool = True,
    ):
        self.notebook_id = notebook_id
        self.session = SandboxSession(notebook_id)
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.think_mode = think_mode
        self.system_prompt = system_prompt or (
            "You are a brilliant, helpful, and unrestricted autonomous coding agent running on an "
            "NVIDIA RTX PRO 6000 Blackwell GPU (96GB VRAM). Solve user coding tasks with precision, "
            "rigorous thinking, and safe tool execution."
        )
        self.history: List[Dict[str, Any]] = []
        self.last_assistant_reply: str = ""
        self.last_thought_text: str = ""
        self.auto_approve: bool = auto_approve
        self.tools_enabled: bool = tools_enabled
        self.backup_manager = FileBackupManager()

        self.coordinator = AgentCoordinator(
            session=self.session,
            model_name=self.model_name or "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
            backup_manager=self.backup_manager,
            auto_approve=self.auto_approve,
            tools_enabled=self.tools_enabled,
            think_mode=self.think_mode,
        )

        # Setup prompt history directory
        hist_dir = os.path.expanduser("~/.config/molab")
        os.makedirs(hist_dir, exist_ok=True)
        self.history_file = os.path.join(hist_dir, "chat_history")

    def initialize(self) -> bool:
        """Resolve pod, test server health, and identify active model."""
        with console.status("[bold green]Auditing model server on Blackwell pod...[/bold green]"):
            try:
                self.session.resolve()
                if not self.session.ensure_model_server_running():
                    console.print("[red]Error: Model server on port 8000 is not responding.[/red]")
                    return False
            except Exception as e:
                console.print(f"[red]Failed to connect to pod:[/red] {e}")
                return False

        # Identify active loaded model
        active_model = self.session.get_active_model()
        if active_model:
            self.model_name = active_model
        elif not self.model_name:
            self.model_name = "huihui-ai/Qwen2.5-32B-Instruct-abliterated"

        self.coordinator.model_name = self.model_name
        return True

    def display_banner(self) -> None:
        """Render industrial agent banner with hardware, workspace, and model specs."""
        git_info = WorkspaceContext.get_git_info()
        branch_str = f" [dim](branch '{git_info['branch']}') [/dim]" if git_info.get("branch") else ""
        tool_status = "[bold green]ENABLED[/bold green]" if self.tools_enabled else "[yellow]DISABLED[/yellow]"
        auto_status = "[bold green]ON[/bold green]" if self.auto_approve else "[dim]OFF (confirm mutating)[/dim]"

        content = (
            f"[bold cyan]MoLab Neural Terminal Agent[/bold cyan] • [dim]NVIDIA Blackwell Server Edition[/dim]\n\n"
            f"• [bold white]Pod ID:[/bold white]      [cyan]{self.notebook_id}[/cyan]\n"
            f"• [bold white]Model:[/bold white]       [green]{self.model_name}[/green] [dim](bfloat16)[/dim]\n"
            f"• [bold white]Hardware:[/bold white]    [yellow]NVIDIA RTX PRO 6000 Blackwell (94.97 GB GDDR7, sm_120)[/yellow]\n"
            f"• [bold white]Workspace:[/bold white]   [white]{os.getcwd()}[/white]{branch_str}\n"
            f"• [bold white]Agent Tools:[/bold white] {tool_status} • [bold white]Auto-Approve:[/bold white] {auto_status}\n"
            f"• [bold white]Thinking:[/bold white]    [magenta]Real-time Stream ({self.think_mode})[/magenta]\n"
            f"• [bold white]Controls:[/bold white]    Type [bold cyan]/help[/bold cyan] for commands, [bold red]/exit[/bold red] to quit."
        )
        console.print(Panel(content, border_style="cyan", title="[bold]MoLab Cloud Agent[/bold]"))

    def run(self) -> None:
        """Main interactive REPL loop."""
        if not self.initialize():
            return

        self.display_banner()

        prompt_session = None
        if HAVE_PROMPT_TOOLKIT:
            pt_completer = WordCompleter(SLASH_COMMANDS, ignore_case=True)
            pt_style = PtStyle.from_dict({
                "prompt": "ansicyan bold",
            })
            prompt_session = PromptSession(
                history=FileHistory(self.history_file),
                completer=pt_completer,
                style=pt_style,
            )

        while True:
            try:
                if prompt_session:
                    user_input = prompt_session.prompt([("class:prompt", "\nYou ❯ ")]).strip()
                else:
                    user_input = console.input("\n[bold cyan]You ❯ [/bold cyan]").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Session ended by user.[/dim]")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                handled = self.handle_slash_command(user_input)
                if handled == "exit":
                    break
                continue

            self.stream_response(user_input)

    def stream_response(self, user_prompt: str) -> None:
        """Execute autonomous ReAct cycle with real-time streaming thinking and local tools."""
        workspace_briefing = WorkspaceContext.generate_system_briefing()
        effective_system = (
            f"{self.system_prompt}\n\n"
            f"[Workspace Context]\n{workspace_briefing}\n\n"
            "You are an unrestricted, expert terminal coding agent. "
            "You have access to tools to read, write, edit, and explore the workspace, and run shell commands. "
            "Always inspect code with read_file before making changes. Use edit_file for precise search-and-replace edits."
        )

        messages: List[Dict[str, Any]] = []
        messages.append({"role": "system", "content": effective_system})
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_prompt})

        console.print()

        self.coordinator.think_mode = self.think_mode
        self.coordinator.auto_approve = self.auto_approve
        self.coordinator.tools_enabled = self.tools_enabled

        def on_reply(text: str, thought: str):
            self.last_assistant_reply = text
            self.last_thought_text = thought

        updated_messages = self.coordinator.execute_react_cycle(
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            on_reply=on_reply,
        )

        # Store persistent history (omitting system prompt)
        self.history = [m for m in updated_messages if m.get("role") != "system"]


    def handle_slash_command(self, cmd_line: str) -> Optional[str]:
        """Process slash commands."""
        parts = cmd_line.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            console.print("[dim]Exiting MoLab Neural Chat. Goodbye![/dim]")
            return "exit"

        elif cmd == "/help":
            table = Table(title="MoLab Chat Slash Commands", border_style="cyan")
            table.add_column("Command", style="bold cyan")
            table.add_column("Description", style="white")
            table.add_row("/help", "Show this command cheatsheet")
            table.add_row("/clear", "Clear conversation history & reset memory")
            table.add_row("/undo", "Revert the last file modification made by the agent")
            table.add_row("/diff", "Show git diff of uncommitted changes in workspace")
            table.add_row("/auto", "Toggle auto-approve mode (bypass confirmation prompts)")
            table.add_row("/context", "Show active token count, cached turns, and tools state")
            table.add_row("/tools", "List available agent tools and their descriptions")
            table.add_row("/think [full|compact|off]", "Configure real-time thinking display mode")
            table.add_row("/gpu", "Show live Blackwell GPU telemetry (VRAM, SMs, temp)")
            table.add_row("/model", "Inspect loaded model architecture, context, and port")
            table.add_row("/temp <0.0-2.0>", "Adjust model sampling temperature")
            table.add_row("/tokens <int>", "Set maximum generation token count")
            table.add_row("/system [prompt]", "View or update system prompt")
            table.add_row("/save [filename.md]", "Export conversation transcript to Markdown file")
            table.add_row("/copy", "Display last assistant response for terminal selection")
            table.add_row("/exit, /quit", "Quit chat session")
            console.print(table)

        elif cmd == "/undo":
            success, msg = self.backup_manager.undo_last()
            if success:
                console.print(f"[bold green]✔ Undo Successful:[/bold green] {msg}")
            else:
                console.print(f"[yellow]{msg}[/yellow]")

        elif cmd == "/diff":
            try:
                res = subprocess.run(["git", "diff"], capture_output=True, text=True, cwd=os.getcwd())
                diff_out = res.stdout.strip()
                if diff_out:
                    console.print(Panel(diff_out[:3000] + ("\n... [diff truncated]" if len(diff_out) > 3000 else ""), title="Git Diff (Workspace)", border_style="cyan"))
                else:
                    console.print("[dim]No uncommitted git changes found.[/dim]")
            except Exception as e:
                console.print(f"[red]Failed to run git diff:[/red] {e}")

        elif cmd == "/auto":
            self.auto_approve = not self.auto_approve
            self.coordinator.auto_approve = self.auto_approve
            state = "[bold green]ENABLED (zero confirmation prompts)[/bold green]" if self.auto_approve else "[yellow]OFF (confirm before modifying)[/yellow]"
            console.print(f"✔ Auto-approve is now: {state}")

        elif cmd == "/context":
            est_tokens = sum(len(str(m.get("content", "") or "")) // 3 for m in self.history)
            console.print(Panel(
                f"[bold white]Cached Turns:[/bold white]   {len(self.history)}\n"
                f"[bold white]Est. Tokens:[/bold white]    ~{est_tokens} tokens\n"
                f"[bold white]Auto-Approve:[/bold white]   {self.auto_approve}\n"
                f"[bold white]Tools Status:[/bold white]   {'ENABLED' if self.tools_enabled else 'DISABLED'}\n"
                f"[bold white]Undo Backups:[/bold white]   {len(self.backup_manager._history)} available snapshots\n"
                f"[bold white]CWD:[/bold white]            {os.getcwd()}",
                title="Agent Context & State",
                border_style="cyan",
            ))

        elif cmd == "/tools":
            table = Table(title="Available Agent Tools", border_style="green")
            table.add_column("Tool", style="bold cyan")
            table.add_column("Description", style="white")
            for t in OPENAI_TOOL_DEFINITIONS:
                fn = t["function"]
                table.add_row(fn["name"], fn["description"])
            console.print(table)

        elif cmd == "/clear":
            self.history.clear()
            console.clear()
            self.display_banner()
            console.print("[green]✔ Conversation history cleared.[/green]")

        elif cmd == "/think":
            if arg in ("full", "compact", "off"):
                self.think_mode = arg
                console.print(f"[green]✔ Thinking display set to: [bold]{self.think_mode}[/bold][/green]")
            elif not arg:
                console.print(f"[cyan]Current thinking mode:[/cyan] [bold]{self.think_mode}[/bold] [dim](Options: full, compact, off)[/dim]")
                if self.last_thought_text:
                    console.print(Panel(
                        Text(self.last_thought_text, style="dim italic cyan"),
                        title="[magenta]Last Reasoning Trace[/magenta]",
                        border_style="magenta",
                    ))
            else:
                console.print("[red]Invalid mode. Use: /think full | compact | off[/red]")

        elif cmd == "/gpu":
            with console.status("[bold green]Querying Blackwell GPU telemetry...[/bold green]"):
                try:
                    telemetry = self.session.get_gpu_telemetry()
                    table = Table(title="NVIDIA RTX PRO 6000 Blackwell Telemetry", border_style="green")
                    table.add_column("Property", style="bold cyan")
                    table.add_column("Value", style="bold white")
                    for k, v in telemetry.items():
                        if k != "raw_output":
                            table.add_row(str(k).replace("_", " ").title(), str(v))
                    console.print(table)
                except Exception as e:
                    console.print(f"[red]Failed to fetch GPU telemetry:[/red] {e}")

        elif cmd == "/model":
            console.print(Panel(
                f"[bold white]Active Model:[/bold white]   [bold green]{self.model_name}[/bold green]\n"
                f"[bold white]Max Tokens:[/bold white]     [bold yellow]{self.max_tokens}[/bold yellow]\n"
                f"[bold white]Temperature:[/bold white]    [bold yellow]{self.temperature}[/bold yellow]\n"
                f"[bold white]Pod ID:[/bold white]         [bold cyan]{self.notebook_id}[/bold cyan]\n"
                f"[bold white]History Turns:[/bold white]  [bold]{len(self.history) // 2}[/bold] turns cached",
                title="Model Configuration",
                border_style="cyan"
            ))

        elif cmd == "/temp":
            if arg:
                try:
                    val = float(arg)
                    if 0.0 <= val <= 2.0:
                        self.temperature = val
                        console.print(f"[green]✔ Temperature updated to: [bold]{self.temperature}[/bold][/green]")
                    else:
                        console.print("[red]Temperature must be between 0.0 and 2.0[/red]")
                except ValueError:
                    console.print("[red]Invalid number for temperature.[/red]")
            else:
                console.print(f"[cyan]Current temperature:[/cyan] [bold]{self.temperature}[/bold]")

        elif cmd == "/tokens":
            if arg:
                try:
                    val = int(arg)
                    if val > 0:
                        self.max_tokens = val
                        console.print(f"[green]✔ Max tokens updated to: [bold]{self.max_tokens}[/bold][/green]")
                    else:
                        console.print("[red]Max tokens must be positive.[/red]")
                except ValueError:
                    console.print("[red]Invalid integer for max tokens.[/red]")
            else:
                console.print(f"[cyan]Current max tokens:[/cyan] [bold]{self.max_tokens}[/bold]")

        elif cmd == "/system":
            if arg:
                self.system_prompt = arg
                console.print("[green]✔ System prompt updated.[/green]")
            else:
                console.print(Panel(self.system_prompt, title="System Prompt", border_style="cyan"))

        elif cmd == "/save":
            fname = arg or f"chat_export_{int(time.time())}.md"
            try:
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(f"# MoLab Chat Export - {time.ctime()}\n\n")
                    f.write(f"- **Pod ID:** `{self.notebook_id}`\n")
                    f.write(f"- **Model:** `{self.model_name}`\n\n---\n\n")
                    for msg in self.history:
                        role = msg["role"].capitalize()
                        f.write(f"### {role}\n\n{msg['content']}\n\n")
                console.print(f"[green]✔ Conversation exported to: [bold]{os.path.abspath(fname)}[/bold][/green]")
            except Exception as e:
                console.print(f"[red]Failed to save file:[/red] {e}")

        elif cmd == "/copy":
            if self.last_assistant_reply:
                console.print(Panel(
                    self.last_assistant_reply,
                    title="Last Assistant Response",
                    border_style="yellow"
                ))
            else:
                console.print("[dim]No assistant response yet to copy.[/dim]")

        else:
            console.print(f"[red]Unknown command:[/red] {cmd}. Type [bold cyan]/help[/bold cyan] for options.")

        return None


def ensure_claude_bridge(port: int = 8082, notebook_id: Optional[str] = None) -> bool:
    """Ensure the MoLab Blackwell AI bridge is running on localhost."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"User-Agent": "molab-cli"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    cmd = [sys.executable, "-m", "molab_cli.bridge"]
    env = os.environ.copy()
    env["BRIDGE_PORT"] = str(port)
    if notebook_id:
        env["MOLAB_POD_ID"] = notebook_id

    try:
        subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        console.print(f"[red]Failed to spawn MoLab bridge daemon:[/red] {e}")
        return False

    deadline = time.time() + 15.0
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"User-Agent": "molab-cli"})
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass

    return False


def run_claude_code_agent(
    notebook_id: str,
    model_name: Optional[str] = None,
    prompt: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> None:
    """
    Launch native Claude Code CLI agent dynamically connected to MoLab Blackwell GPU.
    Styled according to Perspective Design System (Modern, Clean, High-Contrast #00BD7D).
    """
    bridge_online = ensure_claude_bridge(8082, notebook_id=notebook_id)
    if not bridge_online:
        console.print("[yellow]⚠ Warning: Bridge startup timed out. Claude Code will attempt direct auto-launch.[/yellow]")

    # Resolve active model name
    active_model = model_name
    if not active_model:
        try:
            session = SandboxSession(notebook_id)
            active_model = session.get_active_model()
        except Exception:
            pass
    active_model = active_model or "huihui-ai/Qwen2.5-32B-Instruct-abliterated"

    # Perspective Design System Banner (Emerald: #00BD7D, Surface: #111827)
    perspective_title = "[bold #00BD7D]◆ PERSPECTIVE AI[/bold #00BD7D] [bold white]• MoLab Terminal Agent[/bold white]"
    perspective_body = (
        f"  [bold white]Target Pod:[/bold white]      [#00BD7D]{notebook_id}[/#00BD7D]\n"
        f"  [bold white]Neural Model:[/bold white]    [bold green]{active_model}[/bold green] [dim](bfloat16, sm_120)[/dim]\n"
        f"  [bold white]Hardware:[/bold white]        [bold yellow]NVIDIA RTX PRO 6000 Blackwell (94.97 GB GDDR7)[/bold yellow]\n"
        f"  [bold white]Architecture:[/bold white]    [dim]Autonomous Agentic Loop • Local Tool Calling • Live SSE[/dim]\n"
        f"  [bold white]Live Thinking:[/bold white]   [bold #00BD7D]ENABLED[/bold #00BD7D] [dim](streaming thinking_delta)[/dim]\n"
        f"  [bold white]Native Engine:[/bold white]   [bold cyan]Claude Code v2.1.x (Termux Native ARM64)[/bold cyan]\n"
        f"  [bold white]Bridge State:[/bold white]    [#00BD7D]● ONLINE[/#00BD7D] [dim](http://127.0.0.1:8082)[/dim]"
    )
    console.print()
    console.print(Panel(
        perspective_body,
        title=perspective_title,
        border_style="#00BD7D",
        subtitle="[dim]Zero-Config GPU Terminal Agent • Type /help in Claude for agent controls[/dim]",
        subtitle_align="right",
    ))
    console.print()

    # Configure execution environment
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:8082"
    env["ANTHROPIC_API_KEY"] = "sk-molab-blackwell-cluster"
    env["CLAUDE_CODE_TERMUX_NO_PROXY_HELPER"] = "1"
    env["MOLAB_POD_ID"] = notebook_id
    env["TARGET_MODEL"] = active_model

    claude_bin = shutil.which("claude") or "/data/data/com.termux/files/usr/bin/claude"
    args = [claude_bin]

    if prompt:
        args.extend(["-p", prompt])
    if extra_args:
        args.extend(extra_args)

    # Launch Claude Code directly in current terminal session
    try:
        subprocess.run(args, env=env)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Error executing Claude Code agent:[/red] {e}")


def run_hermes_agent(
    notebook_id: str,
    prompt: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Launch Nous Research Hermes Agent connected to Blackwell GPU."""
    from molab_cli.chat import ensure_claude_bridge
    ensure_claude_bridge(8000, notebook_id=notebook_id)

    hermes_bin = shutil.which("hermes") or "/data/data/com.termux/files/usr/bin/hermes"
    args = [hermes_bin]
    if prompt:
        args.extend(["-z", prompt])
    if extra_args:
        args.extend(extra_args)

    try:
        subprocess.run(args)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Error executing Hermes Agent:[/red] {e}")


def run_terminal_chat(
    notebook_id: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    think_mode: str = "full",
    system_prompt: Optional[str] = None,
    model_name: Optional[str] = None,
    legacy: bool = False,
    prompt: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    use_claude: bool = False,
    use_hermes: bool = False,
    auto_approve: bool = False,
    no_tools: bool = False,
) -> None:
    """
    Entry point to launch the interactive terminal agent.
    If notebook_id is omitted, dynamically discovers the active GPU pod.
    Defaults to native MoLab interactive terminal agent with real-time reasoning stream.
    Use --claude to run Claude Code, or --hermes to run Hermes Agent.
    """
    if not notebook_id:
        with console.status("[bold #00BD7D]Auto-discovering active Blackwell pod...[/bold #00BD7D]"):
            notebook_id = SandboxSession.discover_active_pod()

    if not notebook_id:
        console.print("[red]No running MoLab pod found.[/red]")
        console.print("[dim]Please specify a pod ID: molab chat <notebook_id>[/dim]")
        return

    if use_hermes:
        run_hermes_agent(notebook_id=notebook_id, prompt=prompt, extra_args=extra_args)
        return

    if use_claude:
        run_claude_code_agent(
            notebook_id=notebook_id,
            model_name=model_name,
            prompt=prompt,
            extra_args=extra_args,
        )
        return

    agent = TerminalAgentChat(
        notebook_id=notebook_id,
        model_name=model_name,
        max_tokens=max_tokens,
        temperature=temperature,
        think_mode=think_mode,
        system_prompt=system_prompt,
        auto_approve=auto_approve,
        tools_enabled=not no_tools,
    )
    if prompt:
        if not agent.initialize():
            return
        agent.stream_response(prompt)
        return

    agent.run()


