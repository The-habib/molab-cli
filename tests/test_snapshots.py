"""
Unit tests for SnapshotManager and Workspace Checkpoint Engine.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.snapshots import SnapshotError, SnapshotManager


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_jobs.db")


def test_snapshot_db_init(temp_db):
    sm = SnapshotManager(db_path=temp_db)
    with sm._get_conn() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "snapshots" in tables


def test_snapshot_create_and_restore(temp_db, tmp_path):
    sm = SnapshotManager(db_path=temp_db)
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test_123"
    mock_session.sandbox_id = "sb_mock_456"
    mock_session.base_url = "https://sb_mock_456.sb.molab.run"
    mock_session.auth_token = "tok_abc"

    # Mock remote pack output: size, sha256, count
    # Let's create a real dummy local file to simulate downloaded archive
    dummy_archive_content = b"fake-tarball-bytes"
    import hashlib
    content_sha256 = hashlib.sha256(dummy_archive_content).hexdigest()

    mock_session.execute_command.side_effect = [
        f"12345\n{content_sha256}\n42\n",  # pack output
        "",  # rm remote tar
        "42\n",  # unpack output
    ]

    with patch("molab_cli.snapshots.SandboxSession", return_value=mock_session), \
         patch("molab_cli.snapshots.get_snapshots_dir", return_value=str(tmp_path)), \
         patch("subprocess.run") as mock_subproc:

        # Side effect to write dummy file when curl download is called
        def fake_download(cmd, *args, **kwargs):
            if "-o" in cmd:
                dest = cmd[cmd.index("-o") + 1]
                with open(dest, "wb") as f:
                    f.write(dummy_archive_content)
            return MagicMock(returncode=0, stderr="")

        mock_subproc.side_effect = fake_download

        # Test snapshot creation
        snap = sm.create_snapshot("nb_test_123", name="Test Checkpoint")
        assert snap["id"].startswith("snap_")
        assert snap["notebook_id"] == "nb_test_123"
        assert snap["size_bytes"] == 12345
        assert snap["file_count"] == 42
        assert snap["sha256"] == content_sha256
        assert os.path.exists(snap["local_archive_path"])

        # Test snapshot retrieval
        snaps = sm.list_snapshots("nb_test_123")
        assert len(snaps) == 1
        assert snaps[0]["id"] == snap["id"]

        latest = sm.get_latest_snapshot("nb_test_123")
        assert latest is not None
        assert latest["id"] == snap["id"]

        # Test snapshot restoration
        restore_res = sm.restore_snapshot("nb_test_123", snapshot_id=snap["id"])
        assert restore_res["snapshot_id"] == snap["id"]
        assert restore_res["files_restored"] == 42

        # Test snapshot deletion
        deleted = sm.delete_snapshot(snap["id"])
        assert deleted is True
        assert not os.path.exists(snap["local_archive_path"])
        assert sm.get_snapshot(snap["id"]) is None


def test_snapshot_checksum_mismatch(temp_db, tmp_path):
    sm = SnapshotManager(db_path=temp_db)
    mock_session = MagicMock()
    mock_session.notebook_id = "nb_test_123"
    mock_session.sandbox_id = "sb_mock"
    mock_session.base_url = "https://sb_mock.sb.molab.run"
    mock_session.auth_token = "tok_abc"

    mock_session.execute_command.return_value = "100\nremote_hash_123\n5\n"

    with patch("molab_cli.snapshots.SandboxSession", return_value=mock_session), \
         patch("molab_cli.snapshots.get_snapshots_dir", return_value=str(tmp_path)), \
         patch("subprocess.run") as mock_subproc:

        def fake_download(cmd, *args, **kwargs):
            if "-o" in cmd:
                dest = cmd[cmd.index("-o") + 1]
                with open(dest, "wb") as f:
                    f.write(b"corrupt-data")
            return MagicMock(returncode=0, stderr="")

        mock_subproc.side_effect = fake_download

        with pytest.raises(SnapshotError, match="Snapshot checksum mismatch"):
            sm.create_snapshot("nb_test_123")
