"""
Unit tests for MoLab AI Gateway Database & Virtual Key Governance Engine.
"""

import os
import tempfile
import pytest

from molab_cli.gateway_db import GatewayDB, hash_key


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_gateway.db")
        db = GatewayDB(db_path=db_path)
        yield db


def test_db_initialization(temp_db):
    keys = temp_db.list_keys()
    assert len(keys) == 1
    master = keys[0]
    assert master["key_id"] == "key_default_master"
    assert master["name"] == "Default Master Key"
    assert master["is_active"] == 1
    assert master["rpm_limit"] == 120
    assert master["tpm_limit"] == 150000


def test_create_and_validate_key(temp_db):
    raw_key, info = temp_db.create_key(name="test-client-app", rpm_limit=90, tpm_limit=90000)
    assert raw_key.startswith("sk-molab-")
    assert info["name"] == "test-client-app"
    assert info["rpm_limit"] == 90
    assert info["tpm_limit"] == 90000

    # Validate valid key
    validated = temp_db.validate_key(raw_key)
    assert validated is not None
    assert validated["key_id"] == info["key_id"]
    assert validated["name"] == "test-client-app"

    # Validate invalid key
    assert temp_db.validate_key("sk-molab-invalid-key") is None
    assert temp_db.validate_key("") is None


def test_revoke_key(temp_db):
    raw_key, info = temp_db.create_key(name="revokable-app")
    assert temp_db.validate_key(raw_key) is not None

    success = temp_db.revoke_key(info["key_id"])
    assert success is True

    # After revocation, validation must fail
    assert temp_db.validate_key(raw_key) is None

    # Revoking non-existent key returns False
    assert temp_db.revoke_key("key_non_existent") is False


def test_record_usage_and_analytics(temp_db):
    raw_key, info = temp_db.create_key(name="metered-app")
    k_id = info["key_id"]

    temp_db.record_usage(
        key_id=k_id,
        req_id="req_001",
        endpoint="/v1/chat/completions",
        model="Qwen2.5-32B",
        prompt_tokens=150,
        completion_tokens=50,
        ttft_ms=120.5,
        total_duration_ms=450.0,
        status_code=200,
    )

    temp_db.record_usage(
        key_id=k_id,
        req_id="req_002",
        endpoint="/v1/messages",
        model="Qwen2.5-32B",
        prompt_tokens=200,
        completion_tokens=80,
        ttft_ms=110.0,
        total_duration_ms=500.0,
        status_code=200,
    )

    # Check key totals
    validated = temp_db.validate_key(raw_key)
    assert validated["total_requests"] == 2
    assert validated["total_prompt_tokens"] == 350
    assert validated["total_completion_tokens"] == 130

    # Check analytics aggregates
    analytics = temp_db.get_analytics()
    assert analytics["total_requests"] == 2
    assert analytics["total_prompt_tokens"] == 350
    assert analytics["total_completion_tokens"] == 130
    assert analytics["avg_ttft_ms"] > 100.0
    assert analytics["avg_latency_ms"] > 400.0
    assert len(analytics["recent_requests"]) == 2
