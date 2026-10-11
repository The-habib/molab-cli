"""
Unit tests for modular command registry and individual domain command packages.
"""

import click
from click.testing import CliRunner
import pytest

from molab_cli.cli import cli
from molab_cli.commands import ALL_COMMANDS, register_all_commands
from molab_cli.commands.agent import AGENT_COMMANDS
from molab_cli.commands.compute import COMPUTE_COMMANDS
from molab_cli.commands.files import FILES_COMMANDS
from molab_cli.commands.gateway import GATEWAY_COMMANDS
from molab_cli.commands.jobs import JOBS_COMMANDS
from molab_cli.commands.system import SYSTEM_COMMANDS


def test_modular_command_counts():
    assert len(AGENT_COMMANDS) == 6
    assert len(COMPUTE_COMMANDS) == 13
    assert len(FILES_COMMANDS) == 12
    assert len(GATEWAY_COMMANDS) == 12
    assert len(JOBS_COMMANDS) == 4
    assert len(SYSTEM_COMMANDS) == 11
    assert len(ALL_COMMANDS) == 58


def test_register_all_commands_on_fresh_group():
    fresh_group = click.Group("test_fresh")
    register_all_commands(fresh_group)
    assert len(fresh_group.commands) == 58


def test_all_58_commands_registered_on_cli():
    expected_commands = {
        "ui", "web", "dashboard", "chat", "mcp", "gallery",
        "login", "status", "exec", "shell", "install", "doctor", "capabilities", "kernel", "pkg", "keepalive", "permanent",
        "list", "create", "compute", "inspect", "ps", "free", "stop", "delete", "gpu", "usage", "env", "thumbnail", "connections",
        "forward", "bridge", "share", "public", "credentials", "creds", "keys", "stats", "analytics", "perf", "deploy", "serve",
        "push", "pull", "cat", "download", "rename", "clone", "sync", "export", "file", "snapshot", "storage", "vault",
        "job", "workload", "batch", "notify",
    }
    registered = set(cli.commands.keys())
    assert expected_commands.issubset(registered)


def test_command_help_invocations():
    runner = CliRunner()
    for cmd_name in ["chat", "status", "list", "forward", "push", "batch"]:
        res = runner.invoke(cli, [cmd_name, "--help"])
        assert res.exit_code == 0
        assert cmd_name in res.output or "Usage:" in res.output
