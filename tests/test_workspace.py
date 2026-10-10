"""
Unit tests for the Workspace Context Engine.
"""

from molab_cli.workspace import WorkspaceContext


def test_workspace_briefing():
    briefing = WorkspaceContext.generate_system_briefing()
    assert "Workspace CWD:" in briefing
    assert "Environment:" in briefing


def test_top_level_files():
    files = WorkspaceContext.get_top_level_files()
    assert isinstance(files, list)
    assert len(files) > 0
