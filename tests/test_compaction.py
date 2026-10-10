"""
Unit tests for context compaction and output truncation.
"""

from molab_cli.compaction import truncate_tool_output, compact_history


def test_truncate_tool_output_short():
    short_text = "Line 1\nLine 2\nLine 3"
    assert truncate_tool_output(short_text) == short_text


def test_truncate_tool_output_many_lines():
    lines = [f"Output row {i}" for i in range(200)]
    full_text = "\n".join(lines)
    truncated = truncate_tool_output(full_text, max_lines=40)

    assert "Output row 0" in truncated  # Head preserved
    assert "Output row 199" in truncated  # Tail preserved
    assert "lines omitted" in truncated
    assert truncated.count("\n") <= 45


def test_compact_history():
    messages = [
        {"role": "user", "content": "run tests"},
        {"role": "assistant", "content": "running"},
        {"role": "tool", "content": "line 1\n" * 500},  # Huge older output
    ]
    # Replicate to exceed 6 messages
    for i in range(10):
        messages.extend([
            {"role": "user", "content": f"turn {i}"},
            {"role": "assistant", "content": f"reply {i}"},
        ])

    compacted = compact_history(messages, max_active_turns=3)
    # The first tool message should now be summarized
    assert "Prior tool output compacted" in compacted[2]["content"]
