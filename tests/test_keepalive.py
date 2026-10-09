"""
Unit tests for KeepaliveManager and Anti-Idle Heartbeat Subsystem.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.keepalive import KeepaliveError, KeepaliveManager


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_jobs.db")


def test_keepalive_db_init(temp_db):
    km = KeepaliveManager(db_path=temp_db)
    with km._get_conn() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "keepalives" in tables


def test_keepalive_daemon_lifecycle(temp_db, tmp_path):
    km = KeepaliveManager(db_path=temp_db)

    with patch("subprocess.Popen") as mock_popen, \
         patch("molab_cli.keepalive.get_keepalive_log_path", return_value=str(tmp_path / "ka.log")), \
         patch("os.kill") as mock_kill:

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc

        # Start daemon
        rec = km.start_daemon("nb_ka_test", interval=60, max_hours=2.0, auto_restore=True)
        assert rec["notebook_id"] == "nb_ka_test"
        assert rec["pid"] == 99999
        assert rec["status"] == "running"

        # Check status
        # os.kill(99999, 0) succeeds
        mock_kill.return_value = None
        st = km.get_status("nb_ka_test")
        assert st is not None
        assert st["status"] == "running"
        assert st["pid"] == 99999

        # List keepalives
        all_ka = km.list_keepalives()
        assert len(all_ka) == 1
        assert all_ka[0]["notebook_id"] == "nb_ka_test"

        # Stop daemon
        stopped = km.stop_daemon("nb_ka_test")
        assert stopped is True
        st_after = km.get_status("nb_ka_test")
        assert st_after["status"] == "stopped"


def test_keepalive_ping_data_plane():
    km = KeepaliveManager()
    mock_session = MagicMock()
    mock_session.base_url = "https://sb_test.sb.molab.run"
    mock_session.auth_token = "tok_test"

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        assert km.ping_data_plane(mock_session) is True


def test_keepalive_run_loop_restart_detection(temp_db):
    km = KeepaliveManager(db_path=temp_db)

    mock_session = MagicMock()
    mock_session.notebook_id = "nb_restart_test"
    mock_session.expires_at = 1799999999
    # Simulate sandbox_id changing from sb_1 to sb_2 (pod rebooted)
    mock_session.sandbox_id = "sb_2"

    mock_snap_mgr = MagicMock()
    mock_snap_mgr.restore_snapshot.return_value = {
        "snapshot_id": "snap_123",
        "files_restored": 10,
        "duration_seconds": 1.2,
    }

    # Populate initial keepalive DB record with old sandbox_id "sb_1"
    with km._get_conn() as conn:
        conn.execute("""
            INSERT INTO keepalives (
                id, notebook_id, sandbox_id, pid, status, interval_seconds,
                max_hours, auto_restore, started_at, last_heartbeat_at,
                heartbeat_count, restores_triggered, error_message, log_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "ka_nb_restart_test", "nb_restart_test", "sb_1", 1234, "running",
            10, None, 1, 1000.0, 1000.0, 1, 0, None, "/tmp/ka.log"
        ))

    # Test loop iteration with stop requested via signal after 1 iteration
    with patch("molab_cli.keepalive.SandboxSession", return_value=mock_session), \
         patch("molab_cli.keepalive.SnapshotManager", return_value=mock_snap_mgr), \
         patch("molab_cli.keepalive.KeepaliveManager.ping_data_plane", return_value=True), \
         patch("time.sleep", side_effect=KeyboardInterrupt):

        try:
            km.run_loop("nb_restart_test", interval=5, auto_restore=True)
        except KeyboardInterrupt:
            pass

    # Verify auto-restore was invoked
    assert mock_snap_mgr.restore_snapshot.called


def test_is_keepalive_process_validation():
    from molab_cli.keepalive import is_keepalive_process
    from unittest.mock import mock_open

    # Non-keepalive alien process
    with patch("os.kill", return_value=None), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=b"/usr/bin/redis-server\x00")):
        assert is_keepalive_process(1234, "nb_test") is False

    # Valid molab keepalive process
    with patch("os.kill", return_value=None), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=b"python3\x00-m\x00molab_cli.cli\x00keepalive\x00run\x00nb_test\x00")):
        assert is_keepalive_process(1234, "nb_test") is True
