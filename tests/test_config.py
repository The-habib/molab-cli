"""
Unit tests for configuration loading and persistence.
"""

from unittest.mock import patch
import pytest
from molab_cli.config import get_client_cookie, get_org_id, get_session_id, load_config, save_config


def test_load_config_empty(tmp_path):
    with patch("molab_cli.config.CONFIG_FILE", tmp_path / "nonexistent.json"):
        cfg = load_config()
        assert cfg == {}


def test_save_and_load_config(tmp_path):
    cfg_file = tmp_path / "config.json"
    with patch("molab_cli.config.CONFIG_DIR", tmp_path), patch("molab_cli.config.CONFIG_FILE", cfg_file):
        save_config({"client_cookie": "test_token_123", "session_id": "sess_abc"})
        loaded = load_config()
        assert loaded.get("client_cookie") == "test_token_123"
        assert loaded.get("session_id") == "sess_abc"


def test_defaults(tmp_path):
    with patch("molab_cli.config.CONFIG_FILE", tmp_path / "empty.json"):
        assert get_session_id() == ""
        assert get_org_id() == ""
        assert get_client_cookie() == ""
