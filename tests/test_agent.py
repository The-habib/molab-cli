"""
Unit tests for the Autonomous ReAct Agent Loop Coordinator.
"""

from unittest.mock import MagicMock, patch
from molab_cli.agent import ToolCallAccumulator, AgentCoordinator
from molab_cli.tools import FileBackupManager


def test_tool_call_accumulator_streaming_assembly():
    accumulator = ToolCallAccumulator()

    # Simulate streaming chunk 1: tool declaration
    chunk1 = [{"index": 0, "id": "call_123", "type": "function", "function": {"name": "run_shell"}}]
    accumulator.process_delta(chunk1)
    assert accumulator.has_calls

    # Simulate streaming chunk 2: argument piece 1
    chunk2 = [{"index": 0, "function": {"arguments": '{"command":'}}]
    accumulator.process_delta(chunk2)

    # Simulate streaming chunk 3: argument piece 2
    chunk3 = [{"index": 0, "function": {"arguments": ' "echo 42"}'}}]
    accumulator.process_delta(chunk3)

    completed = accumulator.get_completed_calls()
    assert len(completed) == 1
    assert completed[0]["name"] == "run_shell"
    assert completed[0]["arguments"] == {"command": "echo 42"}


def test_agent_coordinator_confirm_action():
    mock_session = MagicMock()
    coordinator = AgentCoordinator(session=mock_session, model_name="test-model", auto_approve=True)

    # Read tools are auto-approved
    assert coordinator.confirm_action("read_file", {"path": "foo.py"}) is True

    # Mutating tools in auto mode are auto-approved
    assert coordinator.confirm_action("run_shell", {"command": "rm -rf foo"}) is True


def test_agent_coordinator_react_cycle_stop():
    mock_session = MagicMock()
    # Mock single turn response with no tool calls
    mock_session.stream_chat_completion.return_value = [
        {"content": "The answer is 42.", "reasoning_content": "", "tool_calls": None, "raw": {}}
    ]

    coordinator = AgentCoordinator(session=mock_session, model_name="test-model", auto_approve=True)
    messages = [{"role": "user", "content": "What is 42?"}]

    res_messages = coordinator.execute_react_cycle(messages)
    assert len(res_messages) == 2
    assert res_messages[1]["role"] == "assistant"
    assert res_messages[1]["content"] == "The answer is 42."
