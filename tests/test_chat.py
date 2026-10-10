"""
Unit tests for the industry-grade terminal chat agent and StreamThoughtExtractor.
"""

from unittest.mock import MagicMock, patch
import pytest

from molab_cli.chat import StreamThoughtExtractor, TerminalAgentChat
from molab_cli.sandbox import SandboxSession


def test_extractor_pure_response():
    extractor = StreamThoughtExtractor()
    deltas = [
        {"content": "Hello "},
        {"content": "world!"},
    ]
    events = []
    for d in deltas:
        events.extend(extractor.process(d))
    events.extend(extractor.flush())

    assert len(events) == 2
    assert events[0] == ("response_chunk", "Hello ")
    assert events[1] == ("response_chunk", "world!")
    assert not extractor.has_thought


def test_extractor_think_tags_single_chunk():
    extractor = StreamThoughtExtractor()
    deltas = [
        {"content": "<think>Thinking about 42</think>The answer is 42."},
    ]
    events = []
    for d in deltas:
        events.extend(extractor.process(d))
    events.extend(extractor.flush())

    assert extractor.has_thought
    types = [e[0] for e in events]
    assert types == ["think_start", "think_chunk", "think_end", "response_chunk"]
    assert events[1] == ("think_chunk", "Thinking about 42")
    assert events[3] == ("response_chunk", "The answer is 42.")


def test_extractor_think_tags_split_chunks():
    extractor = StreamThoughtExtractor()
    deltas = [
        {"content": "Intro: <thi"},
        {"content": "nk>Step 1..."},
        {"content": " Step 2</th"},
        {"content": "ink>Final answer."},
    ]
    events = []
    for d in deltas:
        events.extend(extractor.process(d))
    events.extend(extractor.flush())

    assert extractor.has_thought
    text_chunks = [e[1] for e in events if e[0] in ("think_chunk", "response_chunk")]
    assert "Intro: " in text_chunks
    assert "Step 1..." in text_chunks
    assert "Final answer." in text_chunks


def test_extractor_native_reasoning_content():
    extractor = StreamThoughtExtractor()
    deltas = [
        {"reasoning_content": "Analyze problem"},
        {"reasoning_content": " and formulate solution."},
        {"content": "The result is 100."},
    ]
    events = []
    for d in deltas:
        events.extend(extractor.process(d))
    events.extend(extractor.flush())

    assert extractor.has_thought
    types = [e[0] for e in events]
    assert types == ["think_start", "think_chunk", "think_chunk", "think_end", "response_chunk"]
    assert events[1][1] == "Analyze problem"
    assert events[4][1] == "The result is 100."


def test_slash_command_handling():
    agent = TerminalAgentChat(notebook_id="nb_test_123")
    
    # Test exit
    assert agent.handle_slash_command("/exit") == "exit"
    assert agent.handle_slash_command("/quit") == "exit"

    # Test temp
    agent.handle_slash_command("/temp 0.9")
    assert agent.temperature == 0.9

    # Test tokens
    agent.handle_slash_command("/tokens 2048")
    assert agent.max_tokens == 2048

    # Test think mode
    agent.handle_slash_command("/think compact")
    assert agent.think_mode == "compact"
    agent.handle_slash_command("/think off")
    assert agent.think_mode == "off"
    agent.handle_slash_command("/think full")
    assert agent.think_mode == "full"

    # Test clear
    agent.history.append({"role": "user", "content": "hi"})
    agent.handle_slash_command("/clear")
    assert len(agent.history) == 0


def test_get_active_model_parsing():
    session = SandboxSession("nb_test_123")
    mock_json = '{"data": [{"id": "huihui-ai/Qwen2.5-32B-Instruct-abliterated"}]}'
    with patch.object(session, "execute_command", return_value=mock_json):
        model = session.get_active_model()
        assert model == "huihui-ai/Qwen2.5-32B-Instruct-abliterated"


def test_discover_active_pod():
    mock_client = MagicMock()
    mock_client.get_free_pod.return_value = {"recommended_free_pod": "nb_discovered_456"}
    pod_id = SandboxSession.discover_active_pod(client=mock_client)
    assert pod_id == "nb_discovered_456"
