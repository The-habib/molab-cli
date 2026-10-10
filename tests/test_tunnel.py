"""
Unit tests for PublicTunnelManager and CLI public sharing commands.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from molab_cli.cli import cli
from molab_cli.tunnel import PublicTunnelManager, render_credentials


def test_is_cloudflared_installed():
    with patch("shutil.which", return_value="/usr/bin/cloudflared"):
        assert PublicTunnelManager.is_cloudflared_installed() is True
    with patch("shutil.which", return_value=None):
        assert PublicTunnelManager.is_cloudflared_installed() is False


def test_tunnel_manager_get_active_tunnel_none():
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, "state.json")
        mgr = PublicTunnelManager(config_file=cfg)
        assert mgr.get_active_tunnel() is None


def test_tunnel_manager_get_active_tunnel_alive():
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, "state.json")
        mgr = PublicTunnelManager(config_file=cfg)
        fake_state = {
            "pid": 99999,
            "port": 8000,
            "public_url": "https://test-tunnel.trycloudflare.com",
            "openai_base_url": "https://test-tunnel.trycloudflare.com/v1",
            "api_key": "sk-molab-blackwell-cluster",
            "model": "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
        }
        with open(cfg, "w") as f:
            json.dump(fake_state, f)

        with patch.object(mgr, "_is_pid_alive", return_value=True):
            act = mgr.get_active_tunnel()
            assert act is not None
            assert act["public_url"] == "https://test-tunnel.trycloudflare.com"


def test_tunnel_manager_stop():
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, "state.json")
        mgr = PublicTunnelManager(config_file=cfg)
        with open(cfg, "w") as f:
            json.dump({"pid": 99999, "public_url": "https://test.trycloudflare.com"}, f)

        with patch.object(mgr, "_is_pid_alive", return_value=True), \
             patch("os.kill") as mock_kill, \
             patch("subprocess.run") as mock_run:
            stopped = mgr.stop()
            assert stopped is True
            assert not os.path.exists(cfg)


def test_render_credentials_no_crash():
    state = {
        "pid": 1234,
        "port": 8000,
        "public_url": "https://sample.trycloudflare.com",
        "openai_base_url": "https://sample.trycloudflare.com/v1",
        "anthropic_base_url": "https://sample.trycloudflare.com",
        "api_key": "sk-molab-blackwell-cluster",
        "model": "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
        "pod_id": "nb_test",
        "hardware": "NVIDIA Blackwell",
    }
    render_credentials(state)


def test_cli_share_status():
    runner = CliRunner()
    fake_state = {
        "pid": 1234,
        "public_url": "https://fake.trycloudflare.com",
        "openai_base_url": "https://fake.trycloudflare.com/v1",
        "api_key": "sk-molab-blackwell-cluster",
        "model": "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
        "hardware": "Blackwell",
    }
    with patch("molab_cli.tunnel.PublicTunnelManager.get_active_tunnel", return_value=fake_state):
        res = runner.invoke(cli, ["share", "--status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["public_url"] == "https://fake.trycloudflare.com"


def test_start_tunnel_named_token():
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, "state.json")
        mgr = PublicTunnelManager(config_file=cfg)
        mock_proc = MagicMock()
        mock_proc.pid = 4321
        mock_proc.poll.return_value = None

        with patch.object(mgr, "is_cloudflared_installed", return_value=True), \
             patch.object(mgr, "stop", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"model": "test-m"}).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp

            state = mgr.start_tunnel(port=8000, tunnel_token="my-cf-token", hostname="ai.example.com", timeout=1.0)
            assert state["public_url"] == "https://ai.example.com"
            assert state["openai_base_url"] == "https://ai.example.com/v1"
            assert mock_popen.called
            call_cmd = mock_popen.call_args[0][0]
            assert "run" in call_cmd
            assert "--token" in call_cmd
            assert "my-cf-token" in call_cmd
