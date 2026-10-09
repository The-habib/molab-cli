"""
Unit tests for Capabilities and Doctor subsystem.
"""

from unittest.mock import MagicMock, patch

from molab_cli.capabilities import check_local_disk_space, check_tool_available, discover_capabilities, run_doctor


def test_check_tool_available():
    # Python executable or sh should always be available
    assert check_tool_available("sh") is True
    assert check_tool_available("nonexistent_tool_xyz_123") is False


def test_check_local_disk_space():
    disk = check_local_disk_space()
    assert disk["status"] in ("confirmed", "error")
    if disk["status"] == "confirmed":
        assert disk["free_gb"] >= 0
        assert disk["total_gb"] > 0


def test_discover_capabilities_structure():
    mock_client = MagicMock()
    mock_client.list_notebooks.return_value = [
        {"id": "nb_test_1", "title": "Pod 1", "gpu": "rtxp6000"},
        {"id": "nb_test_2", "title": "Pod 2", "gpu": ""},
    ]
    mock_client.list_running_sandboxes.return_value = ["nb_test_1"]

    with patch("molab_cli.capabilities.get_client_cookie", return_value="dummy_cookie"), \
         patch("molab_cli.capabilities.inspect_auth_status", return_value={"authenticated": True, "user_email": "test@example.com", "session_id": "sess_1"}):
        caps = discover_capabilities(client=mock_client)

        assert "local" in caps
        assert "auth" in caps
        assert "remote" in caps
        assert "transfer" in caps
        assert "execution" in caps
        assert caps["auth"]["status"] == "authenticated"
        assert caps["remote"]["running_pods"] == 1
        assert caps["remote"]["gpu_pods"] == 1


def test_run_doctor_healthy():
    mock_client = MagicMock()
    mock_client.list_notebooks.return_value = [{"id": "nb_1", "gpu": "rtxp6000"}]
    mock_client.list_running_sandboxes.return_value = ["nb_1"]

    with patch("molab_cli.capabilities.get_client_cookie", return_value="cookie"), \
         patch("molab_cli.capabilities.inspect_auth_status", return_value={"authenticated": True, "user_email": "user@example.com"}), \
         patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        report = run_doctor(client=mock_client)
        assert report["status"] in ("HEALTHY", "WARNING")
        assert report["total_checks"] >= 4
