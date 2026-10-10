"""
Unit tests for the Core Agent Toolset (molab_cli.tools).
"""

import os
import tempfile
import pytest

from molab_cli.tools import (
    FileBackupManager,
    tool_run_shell,
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_list_dir,
    tool_grep_search,
    dispatch_tool,
    OPENAI_TOOL_DEFINITIONS,
)


def test_tool_definitions_valid():
    assert len(OPENAI_TOOL_DEFINITIONS) == 6
    names = [t["function"]["name"] for t in OPENAI_TOOL_DEFINITIONS]
    assert "run_shell" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names
    assert "list_dir" in names
    assert "grep_search" in names


def test_run_shell_success():
    res = tool_run_shell("echo 'hello molab'")
    assert "hello molab" in res


def test_run_shell_timeout():
    res = tool_run_shell("sleep 5", timeout=1)
    assert "timed out" in res.lower()


def test_read_and_write_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "sample.txt")
        # Write
        w_res = tool_write_file(test_file, "Line 1\nLine 2\nLine 3\n")
        assert "Successfully wrote" in w_res

        # Read
        r_res = tool_read_file(test_file, offset=1, limit=2)
        assert "1 | Line 1" in r_res
        assert "2 | Line 2" in r_res
        assert "more lines in file" in r_res


def test_edit_file_surgical():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "code.py")
        tool_write_file(test_file, "def foo():\n    return 41\n")

        # Edit
        edit_res = tool_edit_file(test_file, "return 41", "return 42")
        assert "Successfully updated" in edit_res

        with open(test_file, "r") as f:
            content = f.read()
        assert "return 42" in content
        assert "return 41" not in content


def test_edit_file_errors():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "code.py")
        tool_write_file(test_file, "apple\nbanana\napple\n")

        # Missing target
        res1 = tool_edit_file(test_file, "orange", "grape")
        assert "Error: target_content not found" in res1

        # Multiple matches
        res2 = tool_edit_file(test_file, "apple", "grape")
        assert "found 2 times" in res2


def test_backup_manager_undo():
    backup = FileBackupManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "undo_test.txt")

        # Write initial
        tool_write_file(test_file, "Original text", backup_manager=backup)

        # Modify
        tool_edit_file(test_file, "Original text", "Modified text", backup_manager=backup)
        with open(test_file, "r") as f:
            assert f.read() == "Modified text"

        # Undo
        success, msg = backup.undo_last()
        assert success
        assert "Reverted modifications" in msg
        with open(test_file, "r") as f:
            assert f.read() == "Original text"


def test_list_dir_and_grep():
    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "alpha.txt")
        tool_write_file(f1, "needle in a haystack\n")

        # List dir
        listing = tool_list_dir(tmpdir)
        assert "alpha.txt" in listing

        # Grep
        grep_res = tool_grep_search("needle", path=tmpdir)
        assert "needle in a haystack" in grep_res
