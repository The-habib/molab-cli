"""
Unit tests for MoLab Inference Performance Profiler & SRE Telemetry.
"""

from unittest.mock import MagicMock, patch
import pytest

from molab_cli.perf import parse_vllm_prometheus_metrics, query_pod_performance


SAMPLE_PROMETHEUS_OUTPUT = """
# HELP vllm:prefix_cache_queries_total Prefix cache queries, in terms of number of queried tokens.
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="qwen-32b"} 250000.0
# HELP vllm:prefix_cache_hits_total Prefix cache hits, in terms of number of cached tokens.
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{engine="0",model_name="qwen-32b"} 225000.0
# HELP vllm:time_to_first_token_seconds_sum Latency sum
vllm:time_to_first_token_seconds_sum{engine="0",model_name="qwen-32b"} 50.0
vllm:time_to_first_token_seconds_count{engine="0",model_name="qwen-32b"} 100.0
vllm:request_generation_tokens_sum{engine="0",model_name="qwen-32b"} 4500.0
vllm:num_requests_running{engine="0",model_name="qwen-32b"} 1.0
vllm:num_requests_waiting{engine="0",model_name="qwen-32b"} 0.0
vllm:gpu_cache_usage_factor{engine="0",model_name="qwen-32b"} 0.42
"""

SAMPLE_NVIDIA_SMI_OUTPUT = "97887, 86275, 11612, 15, 32, 120.5, 600"


def test_parse_vllm_prometheus_metrics():
    metrics = parse_vllm_prometheus_metrics(SAMPLE_PROMETHEUS_OUTPUT)
    assert metrics["vllm:prefix_cache_queries_total"] == 250000.0
    assert metrics["vllm:prefix_cache_hits_total"] == 225000.0
    assert metrics["vllm:time_to_first_token_seconds_sum"] == 50.0
    assert metrics["vllm:time_to_first_token_seconds_count"] == 100.0
    assert metrics["vllm:request_generation_tokens_sum"] == 4500.0
    assert metrics["vllm:gpu_cache_usage_factor"] == 0.42


def test_query_pod_performance_calculations():
    with patch("molab_cli.perf.SandboxSession") as mock_session_cls:
        mock_instance = MagicMock()
        mock_session_cls.return_value = mock_instance
        mock_session_cls.discover_active_pod.return_value = "nb_mock_pod"

        def mock_execute(cmd, timeout=6.0):
            if "metrics" in cmd:
                return SAMPLE_PROMETHEUS_OUTPUT
            elif "nvidia-smi" in cmd:
                return SAMPLE_NVIDIA_SMI_OUTPUT
            return ""

        mock_instance.execute_command.side_effect = mock_execute

        data = query_pod_performance("nb_mock_pod")
        assert data["pod_id"] == "nb_mock_pod"

        # Check Cache Calculations (225000 / 250000 = 90.0%)
        assert data["prefix_cache"]["hit_rate_pct"] == 90.0
        assert data["prefix_cache"]["efficiency"] == "OPTIMAL"
        assert data["prefix_cache"]["hits"] == 225000

        # Check Latency (50.0 / 100.0 * 1000 = 500.0 ms)
        assert data["latency"]["avg_ttft_ms"] == 500.0
        assert data["latency"]["requests_sampled"] == 100

        # Check GPU Hardware
        assert data["gpu"]["mem_total_mb"] == 97887.0
        assert data["gpu"]["temp_c"] == 32.0
        assert data["gpu"]["power_draw_w"] == 120.5
