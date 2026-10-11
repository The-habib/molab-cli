"""
Modular command registry for MoLab CLI.
"""

from __future__ import annotations

import click
from molab_cli.commands.agent import AGENT_COMMANDS
from molab_cli.commands.compute import COMPUTE_COMMANDS
from molab_cli.commands.files import FILES_COMMANDS
from molab_cli.commands.gateway import GATEWAY_COMMANDS
from molab_cli.commands.jobs import JOBS_COMMANDS
from molab_cli.commands.system import SYSTEM_COMMANDS

ALL_COMMANDS = (
    AGENT_COMMANDS
    + COMPUTE_COMMANDS
    + FILES_COMMANDS
    + GATEWAY_COMMANDS
    + JOBS_COMMANDS
    + SYSTEM_COMMANDS
)


def register_all_commands(cli_group: click.Group) -> None:
    """Register all modular commands onto the root click group."""
    for cmd in ALL_COMMANDS:
        cli_group.add_command(cmd)
