"""
Unit tests for MarimoBackendClient REST and WebSocket client.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from molab_cli.backend import MarimoBackendClient, MarimoBackendError
from molab_cli.sandbox import SandboxSession


@pytest.fixture
def mock_session():
    session = MagicMock(spec=SandboxSession)
    session.sandbox_id = "sb-test1234"
    session.auth_token = "token_abc"
    session.base_url = "https://sb-test1234.sb.molab.run"
    return session


def test_backend_client_init(mock_session):
    client = MarimoBackendClient(mock_session)
    assert client.base_url == "https://sb-test1234.sb.molab.run"
    assert client.token == "token_abc"


def test_get_usage(mock_session):
    client = MarimoBackendClient(mock_session)
    fake_resp = {
        "memory": {
            "total": 171798691840,
            "used": 3526189056,
            "available": 168272502784,
            "percent": 2.05,
        },
        "server": {"memory": 3651039232},
        "kernel": {"memory": None},
        "cpu": {"percent": 0.05},
        "gpu": [
            {
                "index": 0,
                "name": "NVIDIA RTX PRO 6000 Blackwell",
                "memory": {
                    "total": 102641958912,
                    "used": 3145728,
                    "free": 101972967424,
                    "percent": 0.003,
                },
            }
        ],
    }

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = fake_resp

    with patch("httpx.Client.get", return_value=mock_http_resp):
        res = client.get_usage()
        assert res["total_gb"] == 160.0
        assert res["used_gb"] == 3.28
        assert res["free_gb"] == 156.72
        assert res["percent_used"] == 2.05
        assert res["server_memory_mb"] == 3481.9
        assert res["kernel_memory_mb"] == 0.0
        assert res["cpu_percent"] == 5.0
        assert len(res["gpus"]) == 1
        assert res["gpus"][0]["total_gb"] == 95.59


def test_get_server_version(mock_session):
    client = MarimoBackendClient(mock_session)
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.text = '"0.25.1"'

    with patch("httpx.Client.get", return_value=mock_http_resp):
        assert client.get_server_version() == "0.25.1"


def test_list_files(mock_session):
    client = MarimoBackendClient(mock_session)
    fake_files = [
        {"id": "./notebook.py", "path": "./notebook.py", "name": "notebook.py", "isDirectory": False}
    ]
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {"files": fake_files}

    with patch("httpx.Client.post", return_value=mock_http_resp):
        files = client.list_files(".")
        assert len(files) == 1
        assert files[0]["name"] == "notebook.py"


def test_file_details_and_read(mock_session):
    client = MarimoBackendClient(mock_session)
    fake_details = {
        "file": {"path": "./notebook.py", "name": "notebook.py"},
        "contents": "import marimo as mo\n",
        "mimeType": "text/x-python",
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = fake_details

    with patch("httpx.Client.post", return_value=mock_http_resp):
        details = client.file_details("./notebook.py")
        assert details["mimeType"] == "text/x-python"
        content = client.read_file("./notebook.py")
        assert "import marimo" in content


def test_file_operations_cp_mv_rm_update(mock_session):
    client = MarimoBackendClient(mock_session)
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {"success": True}

    with patch("httpx.Client.post", return_value=mock_http_resp):
        assert client.copy_file("src.txt", "dst.txt") is True
        assert client.move_file("src.txt", "dst.txt") is True
        assert client.delete_file("del.txt") is True
        assert client.update_file("file.txt", "new content") is True


def test_search_files(mock_session):
    client = MarimoBackendClient(mock_session)
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {"files": [{"path": "./test.py", "name": "test.py"}]}

    with patch("httpx.Client.post", return_value=mock_http_resp):
        results = client.search_files("test")
        assert len(results) == 1
        assert results[0]["name"] == "test.py"


def test_package_management(mock_session):
    client = MarimoBackendClient(mock_session)
    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"packages": [{"name": "torch", "version": "2.6.0"}]}

    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.json.return_value = {"success": True}

    with patch("httpx.Client.get", return_value=mock_get):
        pkgs = client.list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["name"] == "torch"

    with patch("httpx.Client.post", return_value=mock_post):
        res = client.add_package("rich", upgrade=True)
        assert res["success"] is True
