"""
Autonomous ReAct Agent Loop Coordinator.
Adopted from Hermes Agent (run_agent.py, tool_executor.py) and Claude Code.
Drives multi-turn autonomous coding workflows: streaming thinking -> tool detection ->
tool execution with confirmation/auto-approve -> observation injection -> follow-up completion.
"""

import json
import os
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from molab_cli.compaction import compact_history, truncate_tool_output
from molab_cli.sandbox import SandboxSession
from molab_cli.tools import (
    FileBackupManager,
    OPENAI_TOOL_DEFINITIONS,
    default_backup_manager,
    dispatch_tool,
)
from molab_cli.workspace import WorkspaceContext

console = Console()


class ToolCallAccumulator:
    """Accumulates partial tool_call streaming chunks into complete function call dicts."""

    def __init__(self):
        self.tool_calls: Dict[int, Dict[str, Any]] = {}

    def process_delta(self, raw_tool_calls: List[Dict[str, Any]]) -> None:
        """Process a list of tool_call deltas from a stream chunk."""
        for tc in raw_tool_calls:
            idx = tc.get("index", 0)
            if idx not in self.tool_calls:
                self.tool_calls[idx] = {
                    "id": tc.get("id") or f"call_{idx}_{int(time.time() * 1000)}",
                    "type": tc.get("type", "function"),
                    "name": "",
                    "arguments": "",
                }

            if tc.get("id"):
                self.tool_calls[idx]["id"] = tc["id"]

            fn = tc.get("function")
            if fn:
                if fn.get("name"):
                    self.tool_calls[idx]["name"] = fn["name"]
                if fn.get("arguments"):
                    self.tool_calls[idx]["arguments"] += fn["arguments"]

    def get_completed_calls(self) -> List[Dict[str, Any]]:
        """Return sorted list of parsed tool calls with JSON decoded arguments."""
        calls = []
        for idx in sorted(self.tool_calls.keys()):
            tc = self.tool_calls[idx]
            args_str = tc["arguments"].strip()
            parsed_args = {}
            if args_str:
                try:
                    parsed_args = json.loads(args_str)
                except Exception:
                    # Fallback: simple argument extraction if JSON is malformed
                    parsed_args = {"raw": args_str}
            calls.append({
                "id": tc["id"],
                "name": tc["name"],
                "arguments": parsed_args,
                "raw_arguments": args_str,
            })
        return calls

    @property
    def has_calls(self) -> bool:
        return bool(self.tool_calls)


class AgentCoordinator:
    """
    Manages autonomous agent execution cycles with local tool execution,
    real-time thinking streaming, and confirmation safeguards.
    """

    def __init__(
        self,
        session: SandboxSession,
        model_name: str,
        backup_manager: Optional[FileBackupManager] = None,
        auto_approve: bool = False,
        tools_enabled: bool = True,
        max_turns: int = 15,
        think_mode: str = "full",  # 'full', 'compact', or 'off'
    ):
        self.session = session
        self.model_name = model_name
        self.backup_manager = backup_manager or default_backup_manager
        self.auto_approve = auto_approve
        self.tools_enabled = tools_enabled
        self.max_turns = max_turns
        self.think_mode = think_mode
        self.last_modified_files: List[str] = []

    def confirm_action(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Prompt user for confirmation on modifying actions unless in auto mode."""
        if self.auto_approve:
            return True

        # Read-only tools execute automatically
        if tool_name in ("read_file", "list_dir", "grep_search"):
            return True

        # Mutating tools: run_shell, write_file, edit_file
        console.print()
        if tool_name == "run_shell":
            cmd = args.get("command", "")
            console.print(f"[bold yellow]⚡ Permission Request:[/bold yellow] Run shell command: [bold white]{cmd}[/bold white]")
        elif tool_name == "write_file":
            path = args.get("path", "")
            console.print(f"[bold yellow]⚡ Permission Request:[/bold yellow] Write file: [bold white]{path}[/bold white]")
        elif tool_name == "edit_file":
            path = args.get("path", "")
            console.print(f"[bold yellow]⚡ Permission Request:[/bold yellow] Edit file: [bold white]{path}[/bold white]")

        try:
            choice = console.input("[bold yellow]Allow execution? [Y/n/always]: [/bold yellow]").strip().lower()
            if choice in ("", "y", "yes"):
                return True
            elif choice == "always":
                self.auto_approve = True
                console.print("[green]✔ Auto-approve enabled for this session.[/green]")
                return True
            else:
                console.print("[red]✖ Action cancelled by user.[/red]")
                return False
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]✖ Action cancelled.[/red]")
            return False

    def execute_react_cycle(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        on_reply: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute autonomous ReAct loop until task completion or max turns reached.
        Yields messages list updated with conversation turns and tool results.
        """
        turn_count = 0
        from molab_cli.chat import StreamThoughtExtractor

        while turn_count < self.max_turns:
            turn_count += 1
            extractor = StreamThoughtExtractor()
            accumulator = ToolCallAccumulator()

            thought_chunks: List[str] = []
            response_chunks: List[str] = []

            extra_payload = {}
            if self.tools_enabled:
                extra_payload["tools"] = OPENAI_TOOL_DEFINITIONS

            # Protect context window
            active_messages = compact_history(messages, max_active_turns=10)

            start_t = time.time()
            first_tok_t = None
            total_toks = 0

            try:
                stream_gen = self.session.stream_chat_completion(
                    messages=active_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=self.model_name,
                    extra_payload=extra_payload if extra_payload else None,
                )

                with Live(console=console, refresh_per_second=20, auto_refresh=True) as live:
                    for delta in stream_gen:
                        if first_tok_t is None:
                            first_tok_t = time.time()
                        total_toks += 1

                        # Tool call detection
                        raw_tc = delta.get("tool_calls")
                        if raw_tc:
                            accumulator.process_delta(raw_tc)

                        events = extractor.process(delta)
                        for ev_type, text in events:
                            if ev_type == "think_start":
                                if self.think_mode != "off":
                                    live.update(Panel(
                                        Text("Analyzing and reasoning...", style="dim italic"),
                                        title="[bold magenta]💭 Thinking Process (Live)[/bold magenta]",
                                        border_style="magenta",
                                    ))
                            elif ev_type == "think_chunk":
                                thought_chunks.append(text)
                                if self.think_mode == "full":
                                    current_th = "".join(thought_chunks)
                                    dur = round(time.time() - (extractor.think_start_time or start_t), 1)
                                    live.update(Panel(
                                        Text(current_th, style="dim italic cyan"),
                                        title=f"[bold magenta]💭 Thinking Process ({dur}s • {extractor.thought_tokens_count} tok)[/bold magenta]",
                                        border_style="magenta",
                                    ))
                                elif self.think_mode == "compact":
                                    dur = round(time.time() - (extractor.think_start_time or start_t), 1)
                                    live.update(Text(f"💭 Thinking... ({dur}s • {extractor.thought_tokens_count} tokens)", style="dim magenta"))
                            elif ev_type == "think_end":
                                dur = round(extractor.think_duration, 2)
                                full_th = "".join(thought_chunks)
                                if self.think_mode == "full":
                                    live.update(Panel(
                                        Text(full_th, style="dim italic cyan"),
                                        title=f"[bold magenta]💭 Thought Process (Completed in {dur}s • {extractor.thought_tokens_count} tokens)[/bold magenta]",
                                        border_style="magenta",
                                    ))
                                elif self.think_mode == "compact":
                                    live.update(Text(f"💭 Thought for {dur}s ({extractor.thought_tokens_count} tokens)", style="dim italic magenta"))
                                live.stop()
                                live.start()
                            elif ev_type == "response_chunk":
                                response_chunks.append(text)
                                cur_ans = "".join(response_chunks)
                                live.update(Markdown(cur_ans + " ▋"))

                    trailing = extractor.flush()
                    for ev_type, text in trailing:
                        if ev_type == "think_chunk":
                            thought_chunks.append(text)
                        elif ev_type == "response_chunk":
                            response_chunks.append(text)

                    final_resp = "".join(response_chunks)
                    if final_resp and not accumulator.has_calls:
                        live.update(Markdown(final_resp))
                    live.stop()

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user (Ctrl+C).[/yellow]")
                break
            except Exception as e:
                console.print(f"\n[red]Generation error:[/red] {e}")
                break

            final_text = "".join(response_chunks).strip()
            final_thought = "".join(thought_chunks).strip()

            if on_reply and final_text:
                on_reply(final_text, final_thought)

            # If tool calls were generated, execute them and continue the ReAct loop
            if accumulator.has_calls:
                completed_calls = accumulator.get_completed_calls()

                # Add assistant message with tool calls
                asst_msg = {
                    "role": "assistant",
                    "content": final_text or None,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": c["raw_arguments"],
                            },
                        }
                        for c in completed_calls
                    ],
                }
                messages.append(asst_msg)

                # Execute each tool call
                for tc in completed_calls:
                    fn_name = tc["name"]
                    fn_args = tc["arguments"]
                    call_id = tc["id"]

                    console.print(Panel(
                        f"[bold white]Tool:[/bold white] [bold cyan]{fn_name}[/bold cyan]\n"
                        f"[bold white]Arguments:[/bold white] [dim]{json.dumps(fn_args, indent=2)}[/dim]",
                        title="[bold yellow]⚡ Autonomous Tool Call[/bold yellow]",
                        border_style="yellow",
                    ))

                    allowed = self.confirm_action(fn_name, fn_args)
                    t_start = time.time()
                    if allowed:
                        result_raw = dispatch_tool(fn_name, fn_args, backup_manager=self.backup_manager)
                        t_elapsed = round(time.time() - t_start, 2)
                        truncated_res = truncate_tool_output(result_raw, max_lines=80, max_chars=8000)

                        if fn_name in ("write_file", "edit_file"):
                            p = fn_args.get("path")
                            if p and p not in self.last_modified_files:
                                self.last_modified_files.append(p)

                        console.print(Panel(
                            Text(truncated_res[:500] + ("..." if len(truncated_res) > 500 else ""), style="dim white"),
                            title=f"[bold green]✔ Tool Output ({t_elapsed}s)[/bold green]",
                            border_style="green",
                        ))
                    else:
                        truncated_res = "Execution cancelled by user."
                        console.print("[dim italic]Tool execution skipped.[/dim italic]")

                    # Append tool result observation
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": truncated_res,
                    })

                # Loop back to next ReAct turn
                continue

            else:
                # No tool calls; final answer achieved
                if final_text:
                    messages.append({"role": "assistant", "content": final_text})

                # Display stats badge
                elapsed_total = max(time.time() - start_t, 0.001)
                ttft_ms = round((first_tok_t - start_t) * 1000, 1) if first_tok_t else 0.0
                tok_speed = round(total_toks / elapsed_total, 1)
                console.print(
                    f"[dim]⚡ [bold green]{tok_speed} tok/s[/bold green] | "
                    f"TTFT: [bold cyan]{ttft_ms}ms[/bold cyan] | "
                    f"Tokens: [bold yellow]{total_toks}[/bold yellow] | "
                    f"Elapsed: [bold white]{elapsed_total:.2f}s[/bold white] | "
                    f"Blackwell RTX PRO 6000[/dim]"
                )
                break

        return messages
