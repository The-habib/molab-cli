"""
Unit tests for CoreWeave sandbox session protocol and telemetry parsing.
"""

from unittest.mock import MagicMock, patch
import pytest
from molab_cli.sandbox import SandboxSession


def test_sandbox_session_initialization():
    sess = SandboxSession("nb_123456")
    assert sess.notebook_id == "nb_123456"

    sess_no_prefix = SandboxSession("123456")
    assert sess_no_prefix.notebook_id == "nb_123456"


def test_get_terminal_size():
    sess = SandboxSession("nb_test")
    lines, cols = sess._get_terminal_size()
    assert isinstance(lines, int) and lines > 0
    assert isinstance(cols, int) and cols > 0


def test_install_packages_command_building():
    sess = SandboxSession("nb_test")
    with patch.object(sess, "execute_command", return_value="installed") as mock_exec:
        sess.install_packages(["torch", "transformers"])
        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert "uv pip install torch transformers" in args[0]
