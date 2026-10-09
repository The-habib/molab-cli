"""
Unit tests for Resource-Aware Multi-Pod Scheduler and Manifest Validator.
"""

import pytest

from molab_cli.scheduler import (
    PodScheduler,
    evaluate_pod_for_task,
    validate_manifest,
)


def test_validate_manifest_valid_dag():
    manifest = {
        "version": "1.0",
        "name": "test-pipeline",
        "tasks": [
            {
                "id": "prep",
                "command": "python3 prep.py",
                "requirements": {"min_vram_gb": 0.0, "gpu": False},
            },
            {
                "id": "train",
                "command": "python3 train.py",
                "dependencies": ["prep"],
                "requirements": {"min_vram_gb": 16.0, "gpu": True},
            },
            {
                "id": "eval",
                "command": "python3 eval.py",
                "dependencies": ["train"],
                "requirements": {"min_vram_gb": 8.0, "gpu": True},
            },
        ],
    }

    res = validate_manifest(manifest)
    assert res.valid is True
    assert len(res.errors) == 0
    assert res.task_count == 3
    # Check topological order: prep in stage 1, train in stage 2, eval in stage 3
    assert res.execution_order == [["prep"], ["train"], ["eval"]]


def test_validate_manifest_parallel_tiers():
    manifest = {
        "version": "1.0",
        "name": "parallel-pipeline",
        "tasks": [
            {"id": "t1", "command": "echo 1"},
            {"id": "t2", "command": "echo 2"},
            {"id": "merge", "command": "echo merge", "dependencies": ["t1", "t2"]},
        ],
    }
    res = validate_manifest(manifest)
    assert res.valid is True
    assert res.execution_order[0] == ["t1", "t2"]
    assert res.execution_order[1] == ["merge"]


def test_validate_manifest_cycle_detection():
    manifest = {
        "version": "1.0",
        "name": "cyclic-pipeline",
        "tasks": [
            {"id": "t1", "command": "echo 1", "dependencies": ["t2"]},
            {"id": "t2", "command": "echo 2", "dependencies": ["t1"]},
        ],
    }
    res = validate_manifest(manifest)
    assert res.valid is False
    assert any("Circular dependency" in err for err in res.errors)


def test_validate_manifest_self_dependency():
    manifest = {
        "version": "1.0",
        "tasks": [
            {"id": "t1", "command": "echo 1", "dependencies": ["t1"]},
        ],
    }
    res = validate_manifest(manifest)
    assert res.valid is False
    assert any("cannot depend on itself" in err for err in res.errors)


def test_validate_manifest_missing_dependency():
    manifest = {
        "version": "1.0",
        "tasks": [
            {"id": "t1", "command": "echo 1", "dependencies": ["nonexistent"]},
        ],
    }
    res = validate_manifest(manifest)
    assert res.valid is False
    assert any("nonexistent dependency" in err for err in res.errors)


def test_evaluate_pod_for_task_eligible():
    telemetry = {
        "cuda_available": True,
        "device_name": "NVIDIA RTX PRO 6000",
        "total_vram_gb": 96.0,
        "allocated_vram_gb": 1.5,
        "free_vram_gb": 94.5,
        "is_occupied": False,
        "status": "FREE / IDLE",
    }
    reqs = {"min_vram_gb": 16.0, "gpu": True}
    eval_res = evaluate_pod_for_task("nb_pod1", telemetry, reqs)
    assert eval_res.eligible is True
    assert eval_res.score > 100.0  # idle bonus
    assert any("CUDA GPU verified" in r for r in eval_res.reasons)
    assert any("Free VRAM satisfies requirement" in r for r in eval_res.reasons)


def test_evaluate_pod_for_task_insufficient_vram():
    telemetry = {
        "cuda_available": True,
        "device_name": "NVIDIA GPU",
        "total_vram_gb": 24.0,
        "allocated_vram_gb": 18.0,
        "free_vram_gb": 6.0,
        "is_occupied": False,
    }
    reqs = {"min_vram_gb": 16.0, "gpu": True}
    eval_res = evaluate_pod_for_task("nb_pod2", telemetry, reqs)
    assert eval_res.eligible is False
    assert any("Insufficient free VRAM" in r for r in eval_res.reasons)


def test_evaluate_pod_for_task_occupied():
    telemetry = {
        "cuda_available": True,
        "free_vram_gb": 80.0,
        "is_occupied": True,
    }
    reqs = {"min_vram_gb": 8.0, "gpu": True}
    eval_res = evaluate_pod_for_task("nb_pod3", telemetry, reqs)
    assert eval_res.eligible is False
    assert any("currently occupied" in r for r in eval_res.reasons)


def test_evaluate_pod_for_task_unreachable():
    telemetry = {
        "error": "Pod connection timed out",
        "status": "UNREACHABLE",
    }
    reqs = {"gpu": False}
    eval_res = evaluate_pod_for_task("nb_offline", telemetry, reqs)
    assert eval_res.eligible is False
    assert any("unreachable" in r for r in eval_res.reasons)


def test_scheduler_select_pod():
    scheduler = PodScheduler()
    candidate_pods = {
        "pod_small": {
            "cuda_available": True,
            "device_name": "GPU-Small",
            "free_vram_gb": 10.0,
            "is_occupied": False,
        },
        "pod_big": {
            "cuda_available": True,
            "device_name": "GPU-Big",
            "free_vram_gb": 90.0,
            "is_occupied": False,
        },
    }
    task = {
        "id": "heavy_task",
        "requirements": {"min_vram_gb": 20.0, "gpu": True},
    }
    allocations = {}
    chosen, evals = scheduler.select_pod_for_task(task, candidate_pods, allocations)
    assert chosen == "pod_big"
    assert len(evals) == 2
