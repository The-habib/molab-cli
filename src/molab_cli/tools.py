"""
Core Agent Toolset for MoLab Native Terminal Agent.
Adopted from Hermes Agent (tools/file_tools.py, tools/terminal_tool.py) and Aider.
Provides high-speed local workspace tools: run_shell, read_file, write_file,
edit_file (search & replace), list_dir, grep_search, plus file backup tracking for /undo.
"""

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple


class FileBackupManager:
    """Tracks pre-modification file snapshots in-memory to enable instant /undo."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._history: List[Tuple[str, Optional[str]]] = []  # (abs_path, previous_content or None if was new)

    def record_before_write(self, path: str) -> None:
        """Snapshot file contents before modifying it."""
        abs_p = os.path.abspath(path)
        if os.path.exists(abs_p):
            try:
                with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self._history.append((abs_p, content))
            except Exception:
                pass
        else:
            self._history.append((abs_p, None))

        if len(self._history) > self.max_history:
            self._history.pop(0)

    def undo_last(self) -> Tuple[bool, str]:
        """Revert the most recent file modification."""
        if not self._history:
            return False, "No file modifications to undo in this session."

        abs_p, prev_content = self._history.pop()
        rel_p = os.path.relpath(abs_p, os.getcwd())

        try:
            if prev_content is None:
                # File was newly created, delete it
                if os.path.exists(abs_p):
                    os.remove(abs_p)
                return True, f"Reverted creation: deleted newly created file '{rel_p}'."
            else:
                # Restore previous contents
                with open(abs_p, "w", encoding="utf-8") as f:
                    f.write(prev_content)
                return True, f"Reverted modifications in '{rel_p}' to previous state."
        except Exception as e:
            return False, f"Failed to undo changes for '{rel_p}': {e}"

    @property
    def has_history(self) -> bool:
        return len(self._history) > 0


# Global backup manager instance
default_backup_manager = FileBackupManager()


def tool_run_shell(command: str, timeout: int = 45) -> str:
    """Execute a bash command in the local environment with timeout and clean output."""
    if not command or not command.strip():
        return "Error: Empty command."

    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=max(1, min(timeout, 300)),
            cwd=os.getcwd(),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode

        # Strip ANSI escape codes
        clean_out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", stdout)
        clean_err = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", stderr)

        result_parts = []
        if clean_out.strip():
            result_parts.append(clean_out.strip())
        if clean_err.strip():
            result_parts.append(f"STDERR:\n{clean_err.strip()}")

        output = "\n".join(result_parts).strip()
        if not output:
            output = f"[Command exited with return code {exit_code} (no output)]"

        if exit_code != 0:
            return f"[Command exited with code {exit_code}]\n{output}"
        return output

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"


def tool_read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    """Read file content with 1-based line numbering and pagination budget."""
    abs_p = os.path.abspath(path)
    if not os.path.exists(abs_p):
        return f"Error: File '{path}' does not exist."
    if os.path.isdir(abs_p):
        return f"Error: '{path}' is a directory, not a file."

    try:
        with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading '{path}': {e}"

    total_lines = len(lines)
    if total_lines == 0:
        return f"File '{path}' is empty (0 lines)."

    start_idx = max(1, offset) - 1
    if start_idx >= total_lines:
        return f"Error: offset={offset} is past the end of file '{path}' (total {total_lines} lines)."

    end_idx = min(start_idx + max(1, limit), total_lines)
    selected_lines = lines[start_idx:end_idx]

    formatted = []
    for i, line in enumerate(selected_lines, start=start_idx + 1):
        formatted.append(f"{i:4d} | {line.rstrip(chr(13) + chr(10))}")

    out = "\n".join(formatted)
    if end_idx < total_lines:
        out += f"\n\n[... {total_lines - end_idx} more lines in file. Use offset={end_idx + 1} to read more ...]"

    return out


def tool_write_file(path: str, content: str, backup_manager: Optional[FileBackupManager] = None) -> str:
    """Safely write or overwrite a file atomically, backing up previous content."""
    abs_p = os.path.abspath(path)
    parent = os.path.dirname(abs_p)
    if parent:
        os.makedirs(parent, exist_ok=True)

    mgr = backup_manager or default_backup_manager
    mgr.record_before_write(abs_p)

    try:
        tmp_path = f"{abs_p}.tmp_{int(time.time() * 1000)}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, abs_p)
        rel_p = os.path.relpath(abs_p, os.getcwd())
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to '{rel_p}'."
    except Exception as e:
        return f"Error writing file '{path}': {e}"


def tool_edit_file(
    path: str,
    target_content: str,
    replacement_content: str,
    backup_manager: Optional[FileBackupManager] = None,
) -> str:
    """
    Surgical search-and-replace block editing adopted from Aider and Claude Code.
    Replaces exact target_content with replacement_content without rewriting whole file.
    """
    abs_p = os.path.abspath(path)
    if not os.path.exists(abs_p):
        return f"Error: File '{path}' does not exist."
    if os.path.isdir(abs_p):
        return f"Error: '{path}' is a directory, not a file."

    try:
        with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading '{path}': {e}"

    if not target_content:
        return "Error: target_content cannot be empty."

    match_count = content.count(target_content)
    if match_count == 0:
        first_line = target_content.splitlines()[0] if target_content.splitlines() else target_content
        return (
            f"Error: target_content not found in '{path}'.\n"
            f"Could not find exact text starting with: '{first_line[:80]}'.\n"
            "Please use read_file to inspect the exact lines and indentation."
        )

    if match_count > 1:
        return (
            f"Error: target_content was found {match_count} times in '{path}'.\n"
            "Please include more surrounding context lines in target_content to ensure a unique match."
        )

    mgr = backup_manager or default_backup_manager
    mgr.record_before_write(abs_p)

    new_content = content.replace(target_content, replacement_content, 1)
    try:
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write(new_content)
        rel_p = os.path.relpath(abs_p, os.getcwd())
        return f"Successfully updated '{rel_p}'."
    except Exception as e:
        return f"Error writing updated file '{path}': {e}"


def tool_list_dir(path: str = ".", max_depth: int = 2) -> str:
    """List directory structure up to max_depth."""
    target_dir = os.path.abspath(path)
    if not os.path.exists(target_dir):
        return f"Error: Path '{path}' does not exist."
    if not os.path.isdir(target_dir):
        return f"Error: '{path}' is not a directory."

    lines = []
    base_depth = target_dir.rstrip(os.sep).count(os.sep)

    for root, dirs, files in os.walk(target_dir):
        # Exclude common noisy directories
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "build", "dist", ".git", ".venv")]
        cur_depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth > max_depth:
            continue

        indent = "  " * cur_depth
        rel_root = os.path.relpath(root, target_dir)
        folder_label = "." if rel_root == "." else os.path.basename(root)
        lines.append(f"{indent}📁 {folder_label}/")

        file_indent = "  " * (cur_depth + 1)
        for f in sorted(files)[:35]:
            if not f.startswith("."):
                lines.append(f"{file_indent}📄 {f}")
        if len(files) > 35:
            lines.append(f"{file_indent}... and {len(files) - 35} more files")

        if len(lines) > 200:
            lines.append("... [truncated directory list] ...")
            break

    return "\n".join(lines)


def tool_grep_search(query: str, path: str = ".") -> str:
    """Search for literal query string across workspace files."""
    if not query:
        return "Error: Empty search query."

    target_path = os.path.abspath(path)
    import shutil
    if shutil.which("rg"):
        try:
            cmd = ["rg", "-n", "-i", "--max-count", "10", "--max-depth", "4", query, target_path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                rel_lines = [os.path.relpath(l.split(":", 1)[0], os.getcwd()) + ":" + l.split(":", 1)[1] if ":" in l else l for l in lines[:50]]
                return "\n".join(rel_lines)
            return f"No matches found for '{query}' in '{path}'."
        except Exception:
            pass

    matches = []
    q_lower = query.lower()
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git", ".venv")]
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, start=1):
                        if q_lower in line.lower():
                            rel_p = os.path.relpath(fpath, os.getcwd())
                            matches.append(f"{rel_p}:{line_no}: {line.strip()[:120]}")
                            if len(matches) >= 40:
                                break
            except Exception:
                continue
        if len(matches) >= 40:
            break

    if not matches:
        return f"No matches found for '{query}' in '{path}'."
    return "\n".join(matches)


OPENAI_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Execute a bash shell command in the local workspace. Use for running tests, checking git status, or running build commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command line string to execute in bash.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 45).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents with 1-based line numbers and pagination. Always inspect files before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start line number, 1-indexed (default: 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of lines to read (default: 200).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgically edit a file by replacing an exact block of existing code (target_content) with replacement_content. Fast and precise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "target_content": {
                        "type": "string",
                        "description": "The exact existing text chunk in the file to be replaced. Must match verbatim.",
                    },
                    "replacement_content": {
                        "type": "string",
                        "description": "The new replacement text to insert in place of target_content.",
                    },
                },
                "required": ["path", "target_content", "replacement_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or completely overwrite an existing file with the provided content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full file content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Explore files and directories in the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: current directory '.').",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory depth to traverse (default: 2).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search for text across project files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text substring to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file path to search within (default: '.').",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def dispatch_tool(
    name: str,
    args: Dict[str, Any],
    backup_manager: Optional[FileBackupManager] = None,
) -> str:
    """Dispatch a tool call by function name and arguments."""
    try:
        if name == "run_shell":
            cmd = args.get("command", "")
            timeout = int(args.get("timeout", 45))
            return tool_run_shell(cmd, timeout=timeout)

        elif name == "read_file":
            path = args.get("path", "")
            offset = int(args.get("offset", 1))
            limit = int(args.get("limit", 200))
            return tool_read_file(path, offset=offset, limit=limit)

        elif name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            return tool_write_file(path, content, backup_manager=backup_manager)

        elif name == "edit_file":
            path = args.get("path", "")
            target = args.get("target_content", "")
            replacement = args.get("replacement_content", "")
            return tool_edit_file(path, target, replacement, backup_manager=backup_manager)

        elif name == "list_dir":
            path = args.get("path", ".")
            max_depth = int(args.get("max_depth", 2))
            return tool_list_dir(path, max_depth=max_depth)

        elif name == "grep_search":
            query = args.get("query", "")
            path = args.get("path", ".")
            return tool_grep_search(query, path=path)

        else:
            return f"Error: Unknown tool '{name}'."

    except Exception as e:
        return f"Tool execution error: {e}"
