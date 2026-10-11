"""
Cross-platform compatibility engine for MoLab CLI.
Handles process inspection, signal handling, terminal raw mode,
and filesystem operations across Windows, macOS, and Linux/Android Termux.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:
    psutil = None

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None


def is_process_running(name_or_pattern: str) -> bool:
    """
    Check if a process with the given name or pattern is actively running.
    Uses psutil if available, with OS-specific fallbacks.
    """
    if psutil is not None:
        name_lower = name_or_pattern.lower()
        try:
            for p in psutil.process_iter(attrs=["name", "cmdline"]):
                try:
                    p_name = (p.info.get("name") or "").lower()
                    if name_lower in p_name:
                        return True
                    cmd = " ".join(p.info.get("cmdline") or []).lower()
                    if name_lower in cmd:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            pass
        return False

    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["tasklist", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            return name_or_pattern.lower() in res.stdout.lower()
        except Exception:
            return False
    else:
        try:
            res = subprocess.run(
                ["pgrep", "-f", name_or_pattern],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            return res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            return False


def get_process_pids(name_or_pattern: str) -> List[int]:
    """Return all PIDs matching the process name or command-line pattern."""
    pids: List[int] = []
    if psutil is not None:
        name_lower = name_or_pattern.lower()
        try:
            for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
                try:
                    p_name = (p.info.get("name") or "").lower()
                    cmd = " ".join(p.info.get("cmdline") or []).lower()
                    if name_lower in p_name or name_lower in cmd:
                        pids.append(p.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            pass
        return pids

    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["tasklist", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            for line in res.stdout.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2 and name_or_pattern.lower() in parts[0].lower():
                    try:
                        pids.append(int(parts[1].strip('"')))
                    except ValueError:
                        pass
        except Exception:
            pass
    else:
        try:
            res = subprocess.run(
                ["pgrep", "-f", name_or_pattern],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    try:
                        pids.append(int(line.strip()))
                    except ValueError:
                        pass
        except Exception:
            pass
    return pids


def is_pid_cmdline_matching(pid: int, patterns: List[str]) -> bool:
    """
    Check if process with given PID has command line matching any of patterns.
    Checks /proc if available, falls back to psutil, or returns True if alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    cmdline_path = f"/proc/{pid}/cmdline"
    if os.path.exists(cmdline_path):
        try:
            with open(cmdline_path, "rb") as f:
                cmd = f.read().decode("utf-8", errors="ignore").replace("\x00", " ").lower()
            return any(pat.lower() in cmd for pat in patterns)
        except Exception:
            return False

    if psutil is not None:
        try:
            p = psutil.Process(pid)
            cmd = " ".join(p.cmdline()).lower()
            return any(pat.lower() in cmd for pat in patterns)
        except Exception:
            pass

    return True


def terminate_pid(pid: int, timeout: float = 3.0) -> bool:
    """
    Gracefully terminate process with fallback to kill.
    Handles Windows and POSIX cleanly without throwing unhandled exceptions.
    """
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
                return True
            except psutil.TimeoutExpired:
                proc.kill()
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        except Exception:
            pass

    try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except OSError:
                return True
        sig_kill = getattr(signal, "SIGKILL", signal.SIGTERM)
        os.kill(pid, sig_kill)
        return True
    except OSError:
        return True
    except Exception:
        return False


def get_disk_space(path: str = ".") -> Dict[str, Any]:
    """Retrieve free and total disk space in GB across all platforms."""
    try:
        usage = shutil.disk_usage(path)
        return {
            "status": "confirmed",
            "free_gb": round(usage.free / (1024**3), 2),
            "total_gb": round(usage.total / (1024**3), 2),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_shell_command(command: str) -> List[str]:
    """Resolve the appropriate local shell command line for the OS."""
    bash_bin = shutil.which("bash")
    if not bash_bin and sys.platform == "win32":
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        if os.path.exists(git_bash):
            bash_bin = git_bash

    if bash_bin:
        return [bash_bin, "-c", command]
    elif sys.platform == "win32":
        return ["cmd.exe", "/c", command]
    else:
        return ["sh", "-c", command]


def get_detached_popen_kwargs() -> Dict[str, Any]:
    """Return platform-specific kwargs to spawn a detached background process."""
    if sys.platform == "win32":
        flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        return {"creationflags": flags}
    else:
        return {"start_new_session": True}


def has_termios() -> bool:
    """Return True if termios and tty modules are available."""
    return termios is not None and tty is not None
