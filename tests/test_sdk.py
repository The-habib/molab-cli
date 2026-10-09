"""
Unit tests for MoLab Python SDK and WorkloadRegistry.
"""

from unittest.mock import MagicMock, patch

from molab_cli.sdk import MoLabSDK, Pod
from molab_cli.workloads import WorkloadRegistry


def test_workload_registry():
    reg = WorkloadRegistry()
    workloads = reg.list_workloads()
    names = [w.name for w in workloads]
    assert "video-enhance-4k" in names
    assert "whisper-transcribe" in names
    assert "vllm-serve" in names

    w = reg.get("video-enhance-4k")
    assert w is not None
    assert w.min_vram_gb >= 16.0
    assert w.required_gpu is True


def test_sdk_initialization():
    mock_client = MagicMock()
    sdk = MoLabSDK(client=mock_client)
    assert sdk.job_manager is not None
    assert sdk.workload_registry is not None


def test_pod_instantiation():
    pod = Pod("nb_test_123")
    assert pod.notebook_id == "nb_test_123"
    assert pod.executor is not None
    assert pod.transfer is not None
