"""
Unit tests for native Marimo CLI commands (usage, export, file, kernel, pkg).
"""

import json
from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from molab_cli.cli import cli


def test_cli_usage_command():
    runner = CliRunner()
    fake_usage = {
        "total_gb": 160.0,
        "used_gb": 3.28,
        "free_gb": 156.72,
        "percent_used": 2.05,
        "server_memory_mb": 3481.9,
        "kernel_memory_mb": 0.0,
        "cpu_percent": 5.0,
        "gpus": [
            {
                "index": 0,
                "name": "NVIDIA RTX PRO 6000 Blackwell",
                "total_gb": 95.59,
                "used_gb": 0.0,
                "free_gb": 94.97,
                "percent_used": 0.31,
            }
        ],
    }

    with patch("molab_cli.backend.MarimoBackendClient.get_usage", return_value=fake_usage), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["usage", "nb_test_123", "-j"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["total_gb"] == 160.0
        assert len(data["gpus"]) == 1

        res_table = runner.invoke(cli, ["usage", "nb_test_123"])
        assert res_table.exit_code == 0
        assert "Host Cgroup RAM" in res_table.output
        assert "Blackwell" in res_table.output


def test_cli_file_commands():
    runner = CliRunner()
    fake_files = [
        {"name": "test.txt", "path": "/workspace/test.txt", "isDirectory": False, "size": 128}
    ]

    with patch("molab_cli.backend.MarimoBackendClient.list_files", return_value=fake_files), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["file", "ls", "nb_test_123", "-j"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert len(data) == 1
        assert data[0]["name"] == "test.txt"

    with patch("molab_cli.backend.MarimoBackendClient.read_file", return_value="File content line 1"), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["file", "cat", "nb_test_123", "/workspace/test.txt"])
        assert res.exit_code == 0
        assert "File content line 1" in res.output

    with patch("molab_cli.backend.MarimoBackendClient.copy_file", return_value=True), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["file", "cp", "nb_test_123", "a.txt", "b.txt"])
        assert res.exit_code == 0
        assert "Copied" in res.output


def test_cli_kernel_commands():
    runner = CliRunner()

    with patch("molab_cli.backend.MarimoBackendClient.get_kernel_status", return_value={"state": "idle"}), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["kernel", "status", "nb_test_123"])
        assert res.exit_code == 0
        assert "IDLE" in res.output

    fake_eval = {
        "success": True,
        "stdout": "Executed!\n",
        "stderr": "",
        "output": None,
        "output_text": "42",
    }
    with patch("molab_cli.backend.MarimoBackendClient.eval_python", return_value=fake_eval), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["kernel", "eval", "nb_test_123", "6 * 7"])
        assert res.exit_code == 0
        assert "Executed!" in res.output
        assert "=> 42" in res.output


def test_cli_export_command(tmp_path):
    runner = CliRunner()
    fake_md = "# Exported Notebook\n\n```python\nimport marimo\n```\n"
    out_file = str(tmp_path / "out.md")

    with patch("molab_cli.backend.MarimoBackendClient.export_notebook", return_value=fake_md), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["export", "md", "nb_test_123", "-o", out_file])
        assert res.exit_code == 0
        assert "Exported Markdown" in res.output
        with open(out_file) as f:
            assert f.read() == fake_md


def test_cli_pkg_commands():
    runner = CliRunner()
    fake_pkgs = [{"name": "fastapi", "version": "0.115.0"}]

    with patch("molab_cli.backend.MarimoBackendClient.list_packages", return_value=fake_pkgs), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["pkg", "list", "nb_test_123", "-j"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert len(data) == 1
        assert data[0]["name"] == "fastapi"

    with patch("molab_cli.backend.MarimoBackendClient.add_package", return_value={"success": True}), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        res = runner.invoke(cli, ["pkg", "add", "nb_test_123", "fastapi"])
        assert res.exit_code == 0
        assert "Successfully installed" in res.output
