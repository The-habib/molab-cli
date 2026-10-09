"""
Unit tests for MoLab Web Control Center dashboard.
"""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from molab_cli.web import app

client = TestClient(app)


def test_web_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "MoLab Control Center" in response.text
    assert "NVIDIA RTX PRO 6000" in response.text


def test_web_api_status():
    with patch("molab_cli.web.inspect_auth_status") as mock_auth, \
         patch("molab_cli.web.MoLabClient") as mock_client:
        mock_auth.return_value = {"authenticated": True, "user_email": "test@example.com"}
        instance = mock_client.return_value
        instance.list_running_sandboxes.return_value = {"nb_test": {"sandbox_id": "sb-123"}}

        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["email"] == "test@example.com"
        assert data["active_pods_count"] == 1


def test_web_api_gallery():
    with patch("molab_cli.web.GalleryManager") as mock_gm:
        instance = mock_gm.return_value
        instance.list_templates.return_value = [
            {"slug": "test-model", "title": "Test Model", "url": "https://molab.run/gallery/l/test-model"}
        ]
        instance.search_templates.return_value = [
            {"slug": "test-model", "title": "Test Model", "url": "https://molab.run/gallery/l/test-model"}
        ]

        # List
        res_list = client.get("/api/gallery")
        assert res_list.status_code == 200
        assert len(res_list.json()) == 1

        # Search
        res_search = client.get("/api/gallery?q=test")
        assert res_search.status_code == 200
        assert len(res_search.json()) == 1


def test_web_api_jobs():
    with patch("molab_cli.web.JobManager") as mock_jm:
        instance = mock_jm.return_value
        instance.list_jobs.return_value = [
            {"id": "job_1", "name": "train", "status": "COMPLETED", "command": "python train.py"}
        ]
        instance.get_job_logs.return_value = "Step 1/10 complete..."

        res_jobs = client.get("/api/jobs")
        assert res_jobs.status_code == 200
        assert len(res_jobs.json()) == 1

        res_logs = client.get("/api/jobs/job_1/logs")
        assert res_logs.status_code == 200
        assert "Step 1/10" in res_logs.json()["logs"]
