"""
Workspace Context Engine.
Adopted from Claude Code and Aider.
Automatically discovers workspace git status, project directory layout, and system environment
to generate a compact, token-efficient workspace briefing for the agent system prompt.
"""

import os
import platform
import subprocess
import sys
from typing import Dict, List, Optional


class WorkspaceContext:
    """Discovers local environment and repository structure."""

    @staticmethod
    def get_git_info() -> Dict[str, Optional[str]]:
        """Query active git branch and uncommitted files status."""
        info: Dict[str, Optional[str]] = {"branch": None, "status": None}
        try:
            # Check if in a git repo
            branch_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=os.getcwd(),
            )
            if branch_proc.returncode == 0 and branch_proc.stdout.strip():
                info["branch"] = branch_proc.stdout.strip()

                status_proc = subprocess.run(
                    ["git", "status", "--short"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    cwd=os.getcwd(),
                )
                if status_proc.returncode == 0:
                    lines = [l for l in status_proc.stdout.splitlines() if l.strip()]
                    if not lines:
                        info["status"] = "clean (no uncommitted changes)"
                    else:
                        info["status"] = f"{len(lines)} uncommitted changes"
        except Exception:
            pass
        return info

    @staticmethod
    def get_top_level_files(max_items: int = 25) -> List[str]:
        """Get summary of top-level workspace files and folders."""
        try:
            items = sorted(os.listdir(os.getcwd()))
            filtered = [
                f + ("/" if os.path.isdir(f) else "")
                for f in items
                if not f.startswith(".") and f not in ("__pycache__", "node_modules", ".git", ".venv")
            ]
            return filtered[:max_items]
        except Exception:
            return []

    @classmethod
    def generate_system_briefing(cls) -> str:
        """
        Build a compact context briefing string for injection into the agent system prompt.
        Consumes < 100 tokens.
        """
        cwd = os.getcwd()
        git = cls.get_git_info()
        top_files = cls.get_top_level_files()
        os_info = f"Termux Android ({platform.system()} {platform.machine()})"

        lines = [
            f"• Workspace CWD: {cwd}",
            f"• Environment: {os_info}, Python {sys.version.split()[0]}",
        ]
        if git["branch"]:
            lines.append(f"• Git Repository: branch '{git['branch']}' ({git['status']})")
        if top_files:
            lines.append(f"• Project Items: {', '.join(top_files)}")

        return "\n".join(lines)
