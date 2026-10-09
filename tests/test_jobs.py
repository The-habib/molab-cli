"""
Unit tests for SQLite JobManager subsystem.
"""

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.exceptions import JobNotFoundError
from molab_cli.jobs import JobManager


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile() as tmp:
        yield tmp.name


def test_job_manager_initialization(temp_db):
    mgr = JobManager(db_path=temp_db)
    jobs = mgr.list_jobs()
    assert jobs == []


def test_job_submit_and_get(temp_db):
    mgr = JobManager(db_path=temp_db)

    with patch("molab_cli.jobs.SandboxSession") as mock_session_cls, \
         patch("molab_cli.jobs.RemoteExecutor") as mock_exec_cls:

        mock_session = MagicMock()
        mock_session.sandbox_id = "sb-test-123"
        mock_session_cls.return_value = mock_session

        mock_executor = MagicMock()
        mock_executor.execute_background.return_value = 12345
        mock_exec_cls.return_value = mock_executor

        job = mgr.submit_job(
            notebook_id="nb_test",
            command="echo 'hello'",
            name="unit-test-job",
            workload_type="test",
        )

        assert job["id"].startswith("job_")
        assert job["name"] == "unit-test-job"
        assert job["remote_pid"] == 12345
        assert job["status"] == "RUNNING"

        retrieved = mgr.get_job(job["id"])
        assert retrieved["id"] == job["id"]
        assert retrieved["command"] == "echo 'hello'"


def test_job_not_found(temp_db):
    mgr = JobManager(db_path=temp_db)
    with pytest.raises(JobNotFoundError):
        mgr.get_job("job_nonexistent")


def test_job_cancel(temp_db):
    mgr = JobManager(db_path=temp_db)

    with patch("molab_cli.jobs.SandboxSession"), \
         patch("molab_cli.jobs.RemoteExecutor") as mock_exec_cls:

        mock_executor = MagicMock()
        mock_executor.execute_background.return_value = 999
        mock_exec_cls.return_value = mock_executor

        job = mgr.submit_job(notebook_id="nb_test", command="sleep 60")
        assert job["status"] == "RUNNING"

        cancelled = mgr.cancel_job(job["id"])
        assert cancelled["status"] == "CANCELLED"
