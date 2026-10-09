"""
Unit tests for SQLite Batch Persistence and Multi-Pod Orchestrator execution.
"""

import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.jobs import JobManager
from molab_cli.scheduler import BatchOrchestrator


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile() as tmp:
        yield tmp.name


def test_batch_create_and_get(temp_db):
    jm = JobManager(db_path=temp_db)

    manifest = {
        "version": "1.0",
        "name": "unit-test-pipeline",
        "concurrency_limit": 3,
        "tasks": [
            {"id": "t1", "command": "echo 1", "dependencies": []},
            {"id": "t2", "command": "echo 2", "dependencies": ["t1"]},
        ],
    }

    batch_id = jm.create_batch(manifest)
    assert batch_id.startswith("batch_")

    batch = jm.get_batch(batch_id)
    assert batch["id"] == batch_id
    assert batch["name"] == "unit-test-pipeline"
    assert batch["status"] == "PENDING"
    assert batch["concurrency_limit"] == 3
    assert len(batch["tasks"]) == 2
    assert batch["task_counts"]["total"] == 2
    assert batch["task_counts"]["pending"] == 2


def test_batch_list_and_filter(temp_db):
    jm = JobManager(db_path=temp_db)

    m1 = {"name": "batch-1", "tasks": [{"id": "a", "command": "echo a"}]}
    m2 = {"name": "batch-2", "tasks": [{"id": "b", "command": "echo b"}]}

    b1_id = jm.create_batch(m1)
    b2_id = jm.create_batch(m2)

    batches = jm.list_batches()
    assert len(batches) == 2

    jm.update_batch_status(b1_id, status="COMPLETED")
    completed = jm.list_batches(status="COMPLETED")
    assert len(completed) == 1
    assert completed[0]["id"] == b1_id


def test_batch_cancel(temp_db):
    jm = JobManager(db_path=temp_db)
    manifest = {"name": "cancel-me", "tasks": [{"id": "t1", "command": "sleep 10"}]}
    batch_id = jm.create_batch(manifest)

    # Cancel batch
    cancelled = jm.cancel_batch(batch_id)
    assert cancelled["status"] == "CANCELLED"
    tasks = jm.get_batch_tasks(batch_id)
    assert tasks[0]["status"] == "CANCELLED"


def test_batch_retry(temp_db):
    jm = JobManager(db_path=temp_db)
    manifest = {"name": "retry-me", "tasks": [{"id": "t1", "command": "fail_cmd"}]}
    batch_id = jm.create_batch(manifest)

    task = jm.get_batch_tasks(batch_id)[0]
    jm.update_batch_task(task["id"], status="FAILED", exit_code=1)
    jm.update_batch_status(batch_id, status="FAILED")

    retried = jm.retry_batch(batch_id)
    assert retried["status"] == "PENDING"
    refreshed_tasks = jm.get_batch_tasks(batch_id)
    assert refreshed_tasks[0]["status"] == "PENDING"
    assert refreshed_tasks[0]["exit_code"] is None


def test_orchestrator_pipeline_execution(temp_db):
    jm = JobManager(db_path=temp_db)
    mock_client = MagicMock()
    mock_client.list_running_sandboxes.return_value = ["nb_test_pod"]
    mock_client.list_notebooks.return_value = [{"id": "nb_test_pod", "title": "Test Pod", "gpu": "rtxp6000"}]

    orchestrator = BatchOrchestrator(client=mock_client, job_manager=jm)

    manifest = {
        "version": "1.0",
        "name": "pipeline-flow",
        "tasks": [
            {
                "id": "step1",
                "command": "echo step1",
                "requirements": {"min_vram_gb": 0.0, "gpu": False},
            },
            {
                "id": "step2",
                "command": "echo step2",
                "dependencies": ["step1"],
                "requirements": {"min_vram_gb": 0.0, "gpu": False},
            },
        ],
    }

    batch_id = orchestrator.submit(manifest)

    # Mock scheduler and remote pod inspection
    candidate_telemetry = {
        "nb_test_pod": {
            "notebook_id": "nb_test_pod",
            "cuda_available": True,
            "free_vram_gb": 90.0,
            "is_occupied": False,
            "status": "FREE / IDLE",
        }
    }

    with patch.object(orchestrator.scheduler, "inspect_candidate_pods", return_value=candidate_telemetry), \
         patch.object(jm, "submit_job") as mock_submit, \
         patch.object(jm, "refresh_job_status") as mock_refresh:

        # Step 1 launches job_1, then completes
        mock_submit.side_effect = [
            {"id": "job_01", "status": "RUNNING"},
            {"id": "job_02", "status": "RUNNING"},
        ]
        mock_refresh.side_effect = [
            {"id": "job_01", "status": "COMPLETED", "exit_code": 0},
            {"id": "job_02", "status": "COMPLETED", "exit_code": 0},
        ]

        summary = orchestrator.run_batch(batch_id, poll_interval=0.01)

        assert summary["status"] == "COMPLETED"
        assert summary["task_counts"]["completed"] == 2
        assert mock_submit.call_count == 2
