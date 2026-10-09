"""
Universal Remote Execution Engine for MoLab Cloud Pods.
Handles structured execution, environment variables, working directories,
exit code propagation, timeouts, process tracking, and background jobs.
"""

import asyncio
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import websockets

from molab_cli.exceptions import ExecutionError, ExecutionTimeoutError, SandboxOfflineError
from molab_cli.sandbox import SandboxSession


@dataclass
class ExecutionResult:
    """Structured result of a remote command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
            "is_success": self.is_success,
            "metadata": self.metadata,
        }


class RemoteExecutor:
    """
    Executes commands on an active MoLab sandbox pod with robust session handling.
    """

    def __init__(self, session: SandboxSession):
        self.session = session

    def execute(
        self,
        cmd: str,
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
        retries: int = 1,
    ) -> ExecutionResult:
        """
        Execute a command synchronously with exit code capture and environment setup.
        """
        self.session.resolve()

        # Build composite command prefix for env and workdir
        cmd_parts = []
        if env:
            for k, v in env.items():
                cmd_parts.append(f"export {k}={shlex.quote(str(v))}")
        if workdir:
            cmd_parts.append(f"cd {shlex.quote(workdir)}")
        cmd_parts.append(cmd)
        final_cmd = " && ".join(cmd_parts) if (workdir or env) else cmd

        start_time = time.time()
        attempt = 0
        last_error = None

        while attempt <= retries:
            attempt += 1
            try:
                return asyncio.run(self._run_ws_command(final_cmd, original_cmd=cmd, timeout=timeout, start_time=start_time))
            except (websockets.exceptions.WebSocketException, OSError) as e:
                last_error = e
                if attempt <= retries:
                    time.sleep(1.0)
                    self.session.resolve(force_refresh=True)
                    continue
                break

        duration = time.time() - start_time
        raise ExecutionError(
            f"Failed to execute command on pod {self.session.notebook_id}: {last_error}",
            hint="Check if the pod was shut down or restarted.",
            details={"original_cmd": cmd, "duration": duration},
        )

    async def _run_ws_command(
        self,
        full_cmd: str,
        original_cmd: str,
        timeout: float,
        start_time: float,
    ) -> ExecutionResult:
        uri = self.session.ws_terminal_url
        async with websockets.connect(uri, ping_interval=10, ping_timeout=10) as ws:
            # Drain any pending banner/prompt bytes
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=0.15)
            except asyncio.TimeoutError:
                pass

            uid = uuid.uuid4().hex[:8]
            start_token = f"___EXEC_START_{uid}___"
            s1, s2 = start_token[:10], start_token[10:]
            end_token = f"___EXEC_END_{uid}___"
            e1, e2 = end_token[:8], end_token[8:]

            # Wrapped command prints start token via separate chunks so terminal echo does not match token
            wrapped = (
                f"stty -echo 2>/dev/null; "
                f"printf '%s%s\\n' '{s1}' '{s2}'; "
                f"{full_cmd.strip()}; "
                f"printf '\\n%s%s:%s\\n' '{e1}' '{e2}' '$?'\n"
            )

            await ws.send(wrapped)

            out_chunks: List[str] = []
            timed_out = False
            deadline = time.time() + timeout

            while time.time() < deadline:
                remaining = max(0.2, deadline - time.time())
                try:
                    chunk = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 3.0))
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", errors="replace")
                    out_chunks.append(chunk)
                    full = "".join(out_chunks)
                    if end_token in full:
                        break
                except asyncio.TimeoutError:
                    if time.time() >= deadline:
                        timed_out = True
                        break

            duration = time.time() - start_time
            raw = "".join(out_chunks)
            # Strip ANSI escape codes
            clean = re.sub(r"\x1b\][^\x07\x1b]*\x07|\x1b\[[0-9;?]*[a-zA-Z]", "", raw).replace("\r", "")

            # Extract output and exit code
            s_idx = clean.find(start_token)
            e_idx = clean.find(end_token)
            exit_code = 124 if timed_out else 0
            stdout_text = ""

            if e_idx != -1:
                after_end = clean[e_idx + len(end_token):].strip()
                code_match = re.match(r"^:(\d+)", after_end)
                if code_match:
                    exit_code = int(code_match.group(1))

                if s_idx != -1:
                    stdout_text = clean[s_idx + len(start_token):e_idx].strip()
                else:
                    stdout_text = clean[:e_idx].strip()
            else:
                if s_idx != -1:
                    stdout_text = clean[s_idx + len(start_token):].strip()
                else:
                    stdout_text = clean.strip()

            return ExecutionResult(
                command=original_cmd,
                exit_code=exit_code,
                stdout=stdout_text,
                stderr="",
                duration_seconds=duration,
                timed_out=timed_out,
                metadata={
                    "notebook_id": self.session.notebook_id,
                    "sandbox_id": self.session.sandbox_id,
                },
            )

    def execute_background(
        self,
        cmd: str,
        log_file: str,
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Launch command asynchronously in the background using a remote script and nohup,
        returning its remote PID.
        """
        import base64
        self.session.resolve()
        log_dir = os.path.dirname(log_file)
        if log_dir:
            self.execute(f"mkdir -p {shlex.quote(log_dir)}")

        # Build clean shell script on remote pod to avoid complex quoting and semicolon precedence issues
        env_lines = "\n".join([f"export {k}={shlex.quote(str(v))}" for k, v in env.items()]) if env else ""
        cd_line = f"cd {shlex.quote(workdir)}" if workdir else ""

        runner_sh = f"/tmp/_job_run_{uuid.uuid4().hex[:8]}.sh"
        script_body = (
            "#!/bin/bash\n"
            f"{cd_line}\n"
            f"{env_lines}\n"
            f"{cmd}\n"
        )
        b64_script = base64.b64encode(script_body.encode("utf-8")).decode("utf-8")
        self.execute(f"echo '{b64_script}' | base64 -d > {runner_sh} && chmod +x {runner_sh}", timeout=10.0)

        # Launch detached runner script
        launcher = f"nohup {runner_sh} > {shlex.quote(log_file)} 2>&1 & echo $!"
        res = self.execute(launcher, timeout=12.0)
        out = res.stdout.strip()

        # Find integer PID from last line of stdout
        lines = [line.strip() for line in out.split("\n") if line.strip()]
        for line in reversed(lines):
            if line.isdigit():
                return int(line)

        raise ExecutionError(
            f"Failed to extract background process PID from output: {out}",
            details={"output": out, "command": cmd},
        )

    def is_process_running(self, pid: int) -> bool:
        """Check if remote PID is actively executing."""
        res = self.execute(f"kill -0 {pid} 2>/dev/null && echo 1 || echo 0", timeout=10.0)
        return res.stdout.strip() == "1"

    def read_logs(self, log_file: str, tail_lines: int = 100) -> str:
        """Read trailing lines of remote log file."""
        res = self.execute(f"tail -n {int(tail_lines)} {shlex.quote(log_file)} 2>/dev/null || true", timeout=12.0)
        return res.stdout

    def kill_process(self, pid: int, signal: int = 15) -> bool:
        """Terminate a remote process by PID."""
        res = self.execute(f"kill -{signal} {pid} 2>/dev/null && echo 1 || echo 0", timeout=10.0)
        return res.stdout.strip() == "1"
