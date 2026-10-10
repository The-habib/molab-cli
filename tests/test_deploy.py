"""
Unit tests for MoLab Autonomous 1-Click Deployment Engine.
"""

from unittest.mock import MagicMock, patch
import pytest

from molab_cli.deploy import resolve_model_spec, deploy_model_on_pod, MODEL_CATALOG


def test_resolve_model_spec():
    spec_qwen = resolve_model_spec("qwen-32b")
    assert spec_qwen["model_id"] == "huihui-ai/Qwen2.5-32B-Instruct-abliterated"
    assert spec_qwen["dtype"] == "bfloat16"
    assert spec_qwen["max_model_len"] == 65536

    spec_coder = resolve_model_spec("coder-32b")
    assert "Coder" in spec_coder["model_id"]
    assert spec_coder["tool_call_parser"] == "qwen"

    spec_r1 = resolve_model_spec("r1")
    assert "DeepSeek-R1" in spec_r1["model_id"]

    spec_custom = resolve_model_spec("my-org/custom-model")
    assert spec_custom["model_id"] == "my-org/custom-model"
    assert spec_custom["dtype"] == "bfloat16"


def test_deploy_model_already_active():
    with patch("molab_cli.deploy.SandboxSession") as mock_session_cls, \
         patch("molab_cli.chat.ensure_claude_bridge") as mock_bridge, \
         patch("molab_cli.deploy.PublicTunnelManager") as mock_tunnel_mgr:

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session_cls.discover_active_pod.return_value = "nb_test_pod"
        mock_session.get_active_model.return_value = "huihui-ai/Qwen2.5-32B-Instruct-abliterated"

        mock_mgr_instance = MagicMock()
        mock_tunnel_mgr.return_value = mock_mgr_instance
        mock_mgr_instance.get_active_tunnel.return_value = {
            "public_url": "https://test-tunnel.trycloudflare.com",
            "port": 8000,
            "status": "online",
        }

        res = deploy_model_on_pod("qwen-32b", pod_id="nb_test_pod")

        assert res["status"] == "ready"
        assert res["pod_id"] == "nb_test_pod"
        assert res["model_id"] == "huihui-ai/Qwen2.5-32B-Instruct-abliterated"
        assert res["local_url"] == "http://127.0.0.1:8000/v1"
        assert res["tunnel"]["public_url"] == "https://test-tunnel.trycloudflare.com"

        mock_bridge.assert_called_once_with(8000, notebook_id="nb_test_pod")
