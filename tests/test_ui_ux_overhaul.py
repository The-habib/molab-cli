"""
Comprehensive unit tests verifying UI/UX overhaul, state resolution, secret redaction,
and exception classification.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from molab_cli import __version__
from molab_cli.config import (
    clear_active_notebook_id,
    get_active_notebook_id,
    get_config_dir,
    get_db_path,
    set_active_notebook_id,
)
from molab_cli.exceptions import (
    AuthError,
    ExecutionTimeoutError,
    MissingPodError,
    MoLabError,
    SandboxOfflineError,
    classify_exception,
    redact_sensitive_text,
)
from molab_cli.sandbox import resolve_target_notebook
from molab_cli.theme import (
    handle_cli_error,
    render_banner,
    render_hw_badge,
    render_page_header,
    render_status_badge,
)


def test_secret_redaction():
    text = "Error with cookie __client=super_secret_cookie_value_12345; more info"
    sanitized = redact_sensitive_text(text)
    assert "__client=[REDACTED]" in sanitized
    assert "super_secret_cookie_value_12345" not in sanitized

    text_jwt = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    sanitized_jwt = redact_sensitive_text(text_jwt)
    assert "Bearer [REDACTED_JWT]" in sanitized_jwt

    text_key = "Using virtual API key sk-molab-live-abcdef1234567890 for auth"
    sanitized_key = redact_sensitive_text(text_key)
    assert "sk-molab-li...[REDACTED]" in sanitized_key
    assert "abcdef1234567890" not in sanitized_key


def test_classify_exception_http_401():
    class DummyHTTPError(Exception):
        code = 401

    err = classify_exception(DummyHTTPError("Unauthorized"))
    assert isinstance(err, AuthError)
    assert "re-authenticate" in err.hint.lower()


def test_classify_exception_offline():
    err = classify_exception(RuntimeError("Could not locate active sandbox session for nb_test123"))
    assert isinstance(err, SandboxOfflineError)
    assert err.notebook_id == "nb_test123"
    assert "compute" in err.hint.lower()


def test_classify_exception_timeout():
    err = classify_exception(TimeoutError("Gateway connection timed out after 30s"))
    assert isinstance(err, ExecutionTimeoutError)
    assert "job submit" in err.hint.lower()


def test_config_active_notebook(tmp_path):
    cfg_file = tmp_path / "config.json"
    with patch("molab_cli.config.CONFIG_DIR", tmp_path), patch("molab_cli.config.CONFIG_FILE", cfg_file):
        assert get_active_notebook_id() is None
        set_active_notebook_id("nb_abc123")
        assert get_active_notebook_id() == "nb_abc123"
        clear_active_notebook_id()
        assert get_active_notebook_id() is None


def test_config_get_db_path(tmp_path):
    with patch("molab_cli.config.CONFIG_DIR", tmp_path):
        db = get_db_path("test.db")
        assert db == tmp_path / "test.db"


def test_resolve_target_notebook_explicit():
    mock_client = MagicMock()
    with patch("molab_cli.sandbox.set_active_notebook_id") as mock_set:
        res = resolve_target_notebook(client=mock_client, notebook_id="nb_explicit")
        assert res == "nb_explicit"
        mock_set.assert_called_with("nb_explicit")


def test_resolve_target_notebook_single_running():
    mock_client = MagicMock()
    mock_client.list_running_sandboxes.return_value = {"nb_running_single": "sb-123"}
    with patch("molab_cli.sandbox.get_active_notebook_id", return_value=None):
        with patch("molab_cli.sandbox.set_active_notebook_id") as mock_set:
            res = resolve_target_notebook(client=mock_client)
            assert res == "nb_running_single"
            mock_set.assert_called_with("nb_running_single")


def test_resolve_target_notebook_none_running():
    mock_client = MagicMock()
    mock_client.list_running_sandboxes.return_value = {}
    with patch("molab_cli.sandbox.get_active_notebook_id", return_value=None):
        with pytest.raises(SandboxOfflineError):
            resolve_target_notebook(client=mock_client, require_running=True)


def test_theme_badges_and_headers():
    assert "FREE" in render_status_badge(True, is_free=True)
    assert "OCCUPIED" in render_status_badge(True, is_free=False)
    assert "STOPPED" in render_status_badge(False)
    assert "Blackwell" in render_hw_badge("rtxp6000")
    assert "CPU" in render_hw_badge("")

    # Test banner uses dynamic version
    render_banner()
    render_page_header("Test Title", "Test Subtitle")


def test_handle_cli_error_json(capsys):
    err = MoLabError("Test message", hint="Test hint", code="TEST_CODE")
    with pytest.raises(SystemExit) as excinfo:
        handle_cli_error(err, as_json=True)
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["code"] == "TEST_CODE"
    assert data["message"] == "Test message"
    assert data["hint"] == "Test hint"


def test_get_cached_free_pod():
    from molab_cli.tui import _get_cached_free_pod, _FREE_POD_CACHE
    mock_client = MagicMock()
    
    # Empty pods
    assert _get_cached_free_pod(mock_client, {}) is None
    
    # Single pod
    running = {"nb_1": "sb_1"}
    assert _get_cached_free_pod(mock_client, running) == "nb_1"
    
    # Cached within TTL
    running_multiple = {"nb_1": "sb_1", "nb_2": "sb_2"}
    assert _get_cached_free_pod(mock_client, running_multiple) == "nb_1"

