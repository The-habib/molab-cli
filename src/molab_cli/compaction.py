"""
Context Compaction and Output Truncation Engine.
Adopted from Hermes Agent (turn_truncation.py, trajectory_compressor.py).
Protects the model's 64,000 token context window by truncating oversized command/tool outputs
and compacting older turns in long multi-turn sessions.
"""

from typing import Any, Dict, List, Tuple


def truncate_tool_output(
    output: str,
    max_lines: int = 100,
    max_chars: int = 10_000,
    head_ratio: float = 0.5,
) -> str:
    """
    Truncate oversized tool outputs preserving head (for context) and tail (for outcome/errors).
    Adopted from Hermes Agent turn_truncation.py.
    """
    if not output:
        return ""

    if len(output) <= max_chars and output.count("\n") <= max_lines:
        return output

    lines = output.splitlines()
    total_lines = len(lines)

    if total_lines <= max_lines and len(output) <= max_chars:
        return output

    head_count = int(max_lines * head_ratio)
    tail_count = max_lines - head_count

    if total_lines > max_lines:
        head_lines = lines[:head_count]
        tail_lines = lines[-tail_count:] if tail_count > 0 else []
        omitted_lines = total_lines - head_count - tail_count
        omitted_chars = sum(len(l) for l in lines[head_count:-tail_count])

        truncated_text = (
            "\n".join(head_lines)
            + f"\n\n[... {omitted_lines} lines omitted ({omitted_chars} chars). Use more specific command if needed ...]\n\n"
            + "\n".join(tail_lines)
        )
    else:
        truncated_text = output

    # Byte/character cap
    if len(truncated_text) > max_chars:
        half_char = max_chars // 2 - 100
        truncated_text = (
            truncated_text[:half_char]
            + f"\n\n[... {len(truncated_text) - max_chars} characters omitted ...]\n\n"
            + truncated_text[-half_char:]
        )

    return truncated_text


def compact_history(
    messages: List[Dict[str, Any]],
    max_active_turns: int = 12,
) -> List[Dict[str, Any]]:
    """
    Compacts older conversation turns by summarizing historical tool outputs.
    Preserves recent turns verbatim while shrinking older tool observations.
    """
    if len(messages) <= max_active_turns * 2:
        return messages

    compacted: List[Dict[str, Any]] = []
    # Identify cutoff index for older messages
    cutoff_idx = len(messages) - (max_active_turns * 2)

    for idx, msg in enumerate(messages):
        if idx >= cutoff_idx:
            # Recent turn, keep verbatim
            compacted.append(msg)
            continue

        role = msg.get("role")
        if role == "tool":
            content = str(msg.get("content", ""))
            if len(content) > 300:
                first_line = content.splitlines()[0] if content.splitlines() else content
                summary_content = f"{first_line[:120]} ... [Prior tool output compacted ({len(content)} chars)]"
                new_msg = dict(msg)
                new_msg["content"] = summary_content
                compacted.append(new_msg)
            else:
                compacted.append(msg)
        else:
            compacted.append(msg)

    return compacted
