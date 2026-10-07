"""
Unit tests for Click CLI commands and help flags.
"""

from click.testing import CliRunner
import pytest
from molab_cli.cli import cli


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "molab" in result.output
    assert "1.0.0" in result.output


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "molab" in result.output
    assert "list" in result.output
    assert "create" in result.output
    assert "compute" in result.output
    assert "shell" in result.output
    assert "chat" in result.output
    assert "forward" in result.output


def test_cli_subcommand_helps():
    runner = CliRunner()
    for sub in ["list", "create", "compute", "shell", "exec", "gpu", "chat", "forward", "push", "pull", "install", "stop", "clone", "rename", "delete", "status", "login", "ui"]:
        result = runner.invoke(cli, [sub, "--help"])
        assert result.exit_code == 0, f"Command {sub} --help failed: {result.output}"


def test_theme_render_status_bar():
    from molab_cli.theme import render_banner, render_error_card, render_status_bar
    render_banner()
    render_status_bar({"authenticated": True, "user_email": "test@example.com"}, running_pods_count=1)
    render_status_bar({"authenticated": True, "user_email": "test@example.com"}, running_count=2)
    render_status_bar({"authenticated": False})
    render_error_card("Test Error", "Test Message", hint="Test Hint")
