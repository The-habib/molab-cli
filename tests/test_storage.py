"""
Unit tests for Cloud Storage Bridge (rclone, huggingface-cli, git).
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.storage import StorageBridge, StorageBridgeError


def test_storage_rclone_config_missing():
    sb = StorageBridge("nb_test")
    with patch("os.path.exists", return_value=False):
        with pytest.raises(StorageBridgeError, match="Local rclone configuration file not found"):
            sb.setup_rclone_config("/nonexistent/rclone.conf")


def test_storage_rclone_config_push(tmp_path):
    conf_file = tmp_path / "rclone.conf"
    conf_file.write_text("[r2]\ntype = s3\n")

    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    sb = StorageBridge(mock_session)

    res = sb.setup_rclone_config(str(conf_file))
    assert res is True
    assert mock_session.push_file.called
    assert mock_session.push_file.call_args[0][1] == "/root/.config/rclone/rclone.conf"
    # Ensure chmod 600 executed
    commands = [call[0][0] for call in mock_session.execute_command.call_args_list]
    assert any("chmod 600" in cmd for cmd in commands)


def test_storage_rclone_copy_default():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    mock_session.execute_command.return_value = "Transferred 1.5GB"
    sb = StorageBridge(mock_session)

    # Test non-destructive default (rclone copy)
    res = sb.rclone_sync_to_cloud("r2:bucket/backup", source_dir="/workspace")
    assert "rclone copy" in mock_session.execute_command.call_args[0][0]
    assert res["destructive"] is False
    assert res["operation"] == "copy"

    # Test sync from cloud default (rclone copy)
    res_from = sb.rclone_sync_from_cloud("r2:bucket/backup", target_dir="/workspace")
    assert "rclone copy" in mock_session.execute_command.call_args[0][0]
    assert res_from["destructive"] is False


def test_storage_rclone_sync_destructive():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    mock_session.execute_command.return_value = "Deleted 2 files"
    sb = StorageBridge(mock_session)

    # Test explicit destructive mode (rclone sync)
    res = sb.rclone_sync_to_cloud("r2:bucket/backup", destructive=True)
    assert "rclone sync" in mock_session.execute_command.call_args[0][0]
    assert res["destructive"] is True
    assert res["operation"] == "sync"


def test_storage_rclone_flag_sanitization():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    sb = StorageBridge(mock_session)

    with pytest.raises(StorageBridgeError, match="Forbidden characters"):
        sb.rclone_sync_to_cloud("r2:bucket", extra_flags="--dry-run; rm -rf /")


def test_storage_huggingface_and_git():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    mock_session.execute_command.return_value = "Done with hf_secret_12345678"
    sb = StorageBridge(mock_session)

    # HF Download with token
    res_dl = sb.hf_download("google/gemma-3-27b", dest_dir="/workspace/gemma", token="hf_secret_12345678")
    cmd_dl = mock_session.execute_command.call_args[0][0]
    assert "HF_TOKEN=hf_secret_12345678" in cmd_dl
    assert "--token" not in cmd_dl
    assert "google/gemma-3-27b" in cmd_dl
    assert "--local-dir" in cmd_dl
    assert "/workspace/gemma" in cmd_dl
    # Ensure token redacted in output
    assert "hf_secret_12345678" not in res_dl["output"]
    assert "[REDACTED]" in res_dl["output"]

    # HF Upload
    res_up = sb.hf_upload("/workspace/my_model", "user/repo", repo_type="model", token="hf_secret_12345678")
    cmd_up = mock_session.execute_command.call_args[0][0]
    assert "HF_TOKEN=hf_secret_12345678" in cmd_up
    assert "--token" not in cmd_up
    assert "user/repo" in cmd_up
    assert "/workspace/my_model" in cmd_up

    # Git Clone
    res_git = sb.git_clone("https://github.com/test/repo.git", dest_dir="/workspace/repo", branch="main")
    cmd_git = mock_session.execute_command.call_args[0][0]
    assert "git clone" in cmd_git
    assert "-b" in cmd_git
    assert "main" in cmd_git
    assert "https://github.com/test/repo.git" in cmd_git

