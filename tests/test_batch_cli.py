"""
Unit tests for batch and notification CLI commands.
"""

import json
import tempfile
from unittest.mock import patch

from click.testing import CliRunner

from molab_cli.cli import cli


def test_cli_batch_validate_valid():
    runner = CliRunner()
    manifest = {
        "version": "1.0",
        "name": "cli-test",
        "tasks": [
            {"id": "step1", "command": "echo 1"},
            {"id": "step2", "command": "echo 2", "dependencies": ["step1"]},
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as tmp:
        json.dump(manifest, tmp)
        tmp.flush()

        result = runner.invoke(cli, ["batch", "validate", tmp.name])
        assert result.exit_code == 0
        assert "Manifest is valid" in result.output


def test_cli_batch_validate_json():
    runner = CliRunner()
    manifest = {
        "version": "1.0",
        "name": "cli-test-json",
        "tasks": [{"id": "t1", "command": "echo 1"}],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as tmp:
        json.dump(manifest, tmp)
        tmp.flush()

        result = runner.invoke(cli, ["batch", "validate", tmp.name, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["valid"] is True
        assert data["task_count"] == 1


def test_cli_batch_validate_invalid():
    runner = CliRunner()
    manifest = {
        "version": "1.0",
        "tasks": [
            {"id": "t1", "command": "echo 1", "dependencies": ["t1"]},  # self loop
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as tmp:
        json.dump(manifest, tmp)
        tmp.flush()

        result = runner.invoke(cli, ["batch", "validate", tmp.name])
        assert result.exit_code == 0
        assert "validation failed" in result.output


def test_cli_batch_submit_and_status():
    runner = CliRunner()
    manifest = {
        "version": "1.0",
        "name": "submit-test",
        "tasks": [{"id": "t1", "command": "echo test"}],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as tmp:
        json.dump(manifest, tmp)
        tmp.flush()

        submit_res = runner.invoke(cli, ["batch", "submit", tmp.name, "--json"])
        assert submit_res.exit_code == 0
        submit_data = json.loads(submit_res.output)
        batch_id = submit_data["batch_id"]

        status_res = runner.invoke(cli, ["batch", "status", batch_id, "--json"])
        assert status_res.exit_code == 0
        status_data = json.loads(status_res.output)
        assert status_data["id"] == batch_id
        assert status_data["name"] == "submit-test"


def test_cli_batch_list():
    runner = CliRunner()
    list_res = runner.invoke(cli, ["batch", "list", "--json"])
    assert list_res.exit_code == 0
    batches = json.loads(list_res.output)
    assert isinstance(batches, list)


def test_cli_notify_test():
    runner = CliRunner()
    with patch("molab_cli.notifications.send_webhook") as mock_send:
        mock_send.return_value = {"status": "SENT", "status_code": 200, "error": None}
        result = runner.invoke(cli, ["notify", "test", "--url", "https://example.com/webhook", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "SENT"
        assert data["status_code"] == 200
