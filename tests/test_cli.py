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
    assert "2.3.1" in result.output


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
    for sub in ["list", "create", "compute", "shell", "exec", "gpu", "chat", "forward", "push", "pull", "install", "stop", "clone", "rename", "delete", "status", "login", "ui", "web", "dashboard", "free"]:
        result = runner.invoke(cli, [sub, "--help"])
        assert result.exit_code == 0, f"Command {sub} --help failed: {result.output}"


def test_cli_json_flags():
    from unittest.mock import patch
    runner = CliRunner()
    
    with patch("molab_cli.cli.inspect_auth_status", return_value={"authenticated": True, "user_email": "test@marimo.io"}):
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code == 0
        assert '"authenticated": true' in res.output

    with patch("molab_cli.client.MoLabClient.list_notebooks", return_value=[{"id": "nb_1", "title": "Test", "gpu": "rtxp6000", "running": True}]):
        res = runner.invoke(cli, ["list", "--json"])
        assert res.exit_code == 0
        assert '"nb_1"' in res.output


def test_theme_render_status_bar():
    from molab_cli.theme import render_banner, render_error_card, render_status_bar
    render_banner()
    render_status_bar({"authenticated": True, "user_email": "test@example.com"}, running_pods_count=1)
    render_status_bar({"authenticated": True, "user_email": "test@example.com"}, running_count=2)
    render_status_bar({"authenticated": False})
    render_error_card("Test Error", "Test Message", hint="Test Hint")


def test_cli_unauthenticated():
    from unittest.mock import patch
    runner = CliRunner()
    with patch("molab_cli.config.load_config", return_value={}):
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "Authentication Inactive" in res.output or "molab login" in res.output

        res_json = runner.invoke(cli, ["status", "--json"])
        assert res_json.exit_code == 0
        assert '"authenticated": false' in res_json.output

