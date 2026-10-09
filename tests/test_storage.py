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


def test_storage_rclone_sync():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    mock_session.execute_command.return_value = "Transferred 1.5GB"
    sb = StorageBridge(mock_session)

    # Test sync to cloud
    res = sb.rclone_sync_to_cloud("r2:bucket/backup", source_dir="/workspace")
    assert "rclone sync" in mock_session.execute_command.call_args[0][0]
    assert "r2:bucket/backup" in mock_session.execute_command.call_args[0][0]
    assert res["output"] == "Transferred 1.5GB"

    # Test sync from cloud
    res_from = sb.rclone_sync_from_cloud("r2:bucket/backup", target_dir="/workspace")
    assert "rclone sync" in mock_session.execute_command.call_args[0][0]
    assert "/workspace" in mock_session.execute_command.call_args[0][0]


def test_storage_huggingface_and_git():
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test"
    mock_session.execute_command.return_value = "Done"
    sb = StorageBridge(mock_session)

    # HF Download
    res_dl = sb.hf_download("google/gemma-3-27b", dest_dir="/workspace/gemma")
    cmd_dl = mock_session.execute_command.call_args[0][0]
    assert "huggingface-cli download" in cmd_dl
    assert "google/gemma-3-27b" in cmd_dl
    assert "--local-dir" in cmd_dl
    assert "/workspace/gemma" in cmd_dl

    # HF Upload
    res_up = sb.hf_upload("/workspace/my_model", "user/repo", repo_type="model")
    cmd_up = mock_session.execute_command.call_args[0][0]
    assert "huggingface-cli upload" in cmd_up
    assert "user/repo" in cmd_up
    assert "/workspace/my_model" in cmd_up

    # Git Clone
    res_git = sb.git_clone("https://github.com/test/repo.git", dest_dir="/workspace/repo", branch="main")
    cmd_git = mock_session.execute_command.call_args[0][0]
    assert "git clone" in cmd_git
    assert "-b" in cmd_git
    assert "main" in cmd_git
    assert "https://github.com/test/repo.git" in cmd_git
