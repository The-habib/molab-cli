"""
Unit tests for the MoLab Claude Code Bridge and Anthropic-to-Blackwell adapter.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from molab_cli.bridge import (
    app,
    convert_claude_to_openai,
    parse_tool_call,
    extract_system_prompt,
    extract_content_text,
)


def test_extract_system_prompt():
    assert extract_system_prompt("You are a helpful assistant") == "You are a helpful assistant"
    assert extract_system_prompt([{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]) == "Part 1\n\nPart 2"
    assert extract_system_prompt(None) == ""


def test_extract_content_text():
    assert extract_content_text("Plain text") == "Plain text"
    blocks = [
        {"type": "text", "text": "Run this tool:"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        {"type": "tool_result", "tool_use_id": "toolu_123", "content": "file1.txt\nfile2.txt"},
    ]
    extracted = extract_content_text(blocks)
    assert "Run this tool:" in extracted
    assert "<tool_call>" in extracted
    assert '"name": "Bash"' in extracted
    assert "<tool_response" in extracted
    assert "file1.txt" in extracted


def test_convert_claude_to_openai():
    claude_req = {
        "model": "claude-sonnet-4-5-20250929",
        "system": "System instructions here.",
        "messages": [
            {"role": "user", "content": "How many GPUs?"}
        ],
        "temperature": 0.5,
        "max_tokens": 512,
        "tools": [
            {
                "name": "Bash",
                "description": "Execute command",
                "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}
            }
        ]
    }
    openai_payload = convert_claude_to_openai(claude_req, target_model="qwen-32b")

    assert openai_payload["model"] == "qwen-32b"
    assert openai_payload["temperature"] == 0.5
    assert openai_payload["max_tokens"] == 512
    assert len(openai_payload["messages"]) == 2  # system + user
    assert "System instructions here." in openai_payload["messages"][0]["content"]
    assert "# Available Tools" in openai_payload["messages"][0]["content"]
    assert openai_payload["messages"][1]["role"] == "user"
    assert openai_payload["messages"][1]["content"] == "How many GPUs?"


def test_parse_tool_call_tag():
    text = "Let me list the directory:\n<tool_call>\n{\"name\": \"Bash\", \"arguments\": {\"command\": \"ls -la\"}}\n</tool_call>"
    parsed = parse_tool_call(text)
    assert parsed is not None
    assert parsed["name"] == "Bash"
    assert parsed["arguments"] == {"command": "ls -la"}
    assert parsed["pre_text"] == "Let me list the directory:"


def test_parse_tool_call_json_block():
    text = "Here is the call:\n```json\n{\"name\": \"Edit\", \"arguments\": {\"file\": \"test.py\"}}\n```"
    parsed = parse_tool_call(text)
    assert parsed is not None
    assert parsed["name"] == "Edit"
    assert parsed["arguments"] == {"file": "test.py"}


def test_bridge_health_endpoint():
    from unittest.mock import AsyncMock
    client = TestClient(app)
    with patch("molab_cli.bridge.get_active_session_async", new_callable=AsyncMock) as mock_get_sess:
        mock_sess = MagicMock()
        mock_sess.notebook_id = "nb_test_pod"
        mock_sess.sandbox_id = "sb_test_sandbox"
        mock_get_sess.return_value = (mock_sess, "test-model-32b")

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert data["pod_id"] == "nb_test_pod"
        assert data["model"] == "test-model-32b"
        assert "Blackwell" in data["hardware"]


def test_bridge_models_endpoint():
    from unittest.mock import AsyncMock
    client = TestClient(app)
    with patch("molab_cli.bridge.get_active_session_async", new_callable=AsyncMock) as mock_get_sess:
        mock_sess = MagicMock()
        mock_get_sess.return_value = (mock_sess, "huihui-ai/Qwen2.5-32B-Instruct-abliterated")

        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "claude-sonnet-4-5-20250929" in model_ids
        assert "claude-3-7-sonnet-20250219" in model_ids


def test_bridge_count_tokens():
    client = TestClient(app)
    payload = {
        "system": "Short system prompt",
        "messages": [
            {"role": "user", "content": "Explain quantum computing in detail please."}
        ]
    }
    resp = client.post("/v1/messages/count_tokens", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "input_tokens" in data
    assert data["input_tokens"] > 0
